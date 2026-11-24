"""会话消息与模型、工具之间的执行循环。

``_execute`` 是唯一的执行内核：确认拦截、域外闸门、预算、上下文裁剪、
模型调用、工具消息配对、确定性回复和 Trace 都只在这里实现一次。
``run_agent`` 与 ``stream_agent`` 只是同一个内核的两种输出通道，
流式负责实时吐出 token，非流式负责取回最终回复。
"""

import json
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from time import monotonic
from types import SimpleNamespace

from app.agent.confirmation_intent import classify_confirmation_intent, confirmation_command
from app.agent.model_client import request_model, stream_model
from app.agent.prompts import PENDING_QUESTION_PROMPT
from app.agent.scope import SCOPE_REFUSAL, off_topic_reason
from app.business.service import (
    approve_cancel_confirmation,
    approve_refund_confirmation,
    approve_withdraw_confirmation,
    check_cancel_eligibility,
)
from app.business.tools import TOOLS
from app.harness.budget import (
    TIME_BUDGET_ANSWER,
    TOOL_BUDGET_ANSWER,
    ExecutionBudget,
)
from app.harness.confirmation import load_active_confirmation, reject_confirmation
from app.harness.context import trim_messages
from app.harness.order_selection import (
    asks_cancel_eligibility,
    cancel_amount_cents,
    order_reference,
    requires_cancel_lookup,
    selected_order,
)
from app.harness.tool_executor import PENDING_QUESTION_TOOLS, PENDING_TOOL_NOT_ALLOWED, execute_tool
from app.observability.tracing import emit_trace, new_run_id

PENDING_QUESTION_TOOL_SCHEMAS = [
    tool for tool in TOOLS if tool["function"]["name"] in PENDING_QUESTION_TOOLS
]


@dataclass
class ConversationState:
    """跨轮保留的订单选择和指代状态。"""

    cancel_selection: dict | None = None
    last_queried_order_id: str | None = None
    pending_clarification: dict | None = None

    def discard_finished_clarification(self, active: dict | None) -> None:
        clarification = self.pending_clarification
        if clarification is not None and (
            active is None or active["status"] != "pending"
            or active["confirmation_id"] != clarification["confirmation_id"]
        ):
            self.pending_clarification = None


def create_messages(system_prompt: str) -> list:
    """每段新对话创建独立的消息列表。"""
    return [{"role": "system", "content": system_prompt}]


TOOL_FAILURE_ANSWERS = {
    "TOOL_TIMEOUT": (
        "本次工具调用超时，结果未确认。请稍后重试；"
        "如果涉及取消或退款，请先查询订单和退款状态，不要重复提交。"
    ),
    "TOOL_BUSY": "当前数据库繁忙，本次操作未完成。请稍后重试。",
    "TOOL_ERROR": "工具执行失败，本次操作未完成。请稍后重试。",
    "ORDER_SELECTION_MISMATCH": "查询的订单号与按金额找到的订单不一致。请核对后提供订单号。",
}


def _tool_failure_answer(result: dict) -> str | None:
    """只处理工具执行边界错误；业务拒绝仍交给模型解释。"""
    return TOOL_FAILURE_ANSWERS.get(result.get("error"))


def _single_tool_failure_answer(tool_calls: list, tool_results: list) -> str | None:
    """单个工具失败时直接给出确定性回复，避免模型编造成功结果。"""
    if len(tool_calls) != 1 or len(tool_results) != 1:
        return None
    return _tool_failure_answer(tool_results[0])


def _order_lookup_answer(result: dict) -> str:
    yuan, cents = divmod(result["paid_amount_cents"], 100)
    amount = f"{yuan}.{cents:02d}元" if cents else f"{yuan}元"
    orders = result["orders"]
    if not orders:
        if result["offset"]:
            return f"{amount}的订单候选已显示完。你可以告诉我其中一个订单号。"
        return f"没有找到实付{amount}的本人订单。请核对金额，或提供订单号。"

    if "cancel_reason" in result:
        candidates = "\n".join(
            f"{index}. {order['order_id']}（付款时间：{order['paid_at'] or '未记录'}）"
            for index, order in enumerate(orders, start=1)
        )
        more = "你也可以说“看更多”查看下一页。" if result["has_more"] else ""
        return (
            f"实付{amount}的订单候选如下。请回复订单号或“选第二笔”等序号，"
            f"也可以先询问取消政策，或回复“算了”退出选择。{more}\n{candidates}"
        )

    candidates = "\n".join(
        f"- {order['order_id']}（付款时间：{order['paid_at'] or '未记录'}）" for order in orders
    )
    answer = f"实付{amount}的订单候选如下。请选择要查询物流的订单号。\n{candidates}"
    if result["has_more"]:
        answer += "\n还有其他候选，你也可以说“看更多”。"
    return answer


def _confirmation_answer(record: dict) -> str:
    if record.get("tool") == "cancel_order":
        return (
            f"{record['summary']}\n确认编号：{record['confirmation_id']}\n"
            f"有效期至：{record['expires_at']}\n"
            "本次取消尚未执行。你可以先提问，也可以说“确认取消”或“算了，不取消了”。"
        )
    if record.get("tool") == "cancel_refund":
        return (
            f"{record['summary']}\n确认编号：{record['confirmation_id']}\n"
            f"有效期至：{record['expires_at']}\n"
            "当前退款申请尚未撤回。你可以先提问，也可以说“确认撤回”或“算了，不撤了”。"
        )
    return (
        f"{record['summary']}\n"
        f"确认编号：{record['confirmation_id']}\n"
        f"有效期至：{record['expires_at']}\n"
        "当前尚未创建退款申请。"
        "你可以先提问，也可以说“确认提交”或“算了，不申请了”。"
    )


def format_confirmation_result(*, tool: str, result: dict) -> str:
    """把批准或拒绝的服务端结果转换为用户可读回复。"""
    is_cancel = tool == "cancel_order"
    is_withdraw = tool == "cancel_refund"

    if result.get("status") == "rejected":
        if is_cancel:
            return "已拒绝本次取消请求，本次未修改订单。"
        if is_withdraw:
            return "已拒绝本次撤回请求，退款申请保持原状态。"
        return "已拒绝当前操作，未创建退款申请。"

    if is_cancel and result.get("ok") and result.get("status") == "canceled":
        answer = f"订单 {result['order_id']} 已取消。"
        refund = result["refund"]
        if refund is None:
            return answer + "未付款订单无需退款，未生成退款流水。"
        amount = refund["amount_cents"]
        return (
            answer + f"\n模拟取消退款流水：{refund['refund_id']}\n"
            f"金额：{amount // 100}.{amount % 100:02d}元，流水状态：已完成。"
            "这是本地模拟结果，未发生真实资金划转。"
        )

    if is_withdraw and result.get("ok") and result.get("status") == "withdrawn":
        amount = result["amount_cents"]
        return (
            f"订单 {result['order_id']} 的退款申请 {result['refund_id']} 已撤回。\n"
            f"申请金额：{amount // 100}.{amount % 100:02d} 元\n"
            "订单状态未改变；如需退款，可以重新提交申请。"
        )

    if result.get("ok") and result.get("status") == "submitted":
        amount = result["amount_cents"]
        return (
            f"订单 {result['order_id']} 的退款申请已提交。\n"
            f"申请编号：{result['refund_id']}\n"
            f"申请金额：{amount // 100}.{amount % 100:02d} 元\n"
            "申请状态：待处理。提交申请不代表退款已到账。"
        )

    error = result.get("error", "INTERNAL_ERROR")
    if error == "CONFIRMATION_EXPIRED":
        if is_cancel:
            return "当前取消操作已过期，本次未修改订单。请重新发起取消。"
        if is_withdraw:
            return "当前撤回操作已过期，退款申请保持原状态。请重新发起撤回。"
        return "当前操作已过期，未创建退款申请。请重新发起申请。"
    if error == "CONFIRMATION_STALE":
        if is_cancel:
            return (
                "订单状态或付款信息已变化，本次未执行取消。"
                "请先拒绝当前操作，再重新发起取消以核对新摘要。"
            )
        if is_withdraw:
            return (
                "退款申请金额已变化，本次未执行撤回。"
                "请先拒绝当前操作，再重新发起撤回以核对新金额。"
            )
        return (
            "订单金额已变化，未提交退款申请。"
            "请先拒绝当前操作，再重新发起申请以核对新金额。"
        )
    if is_withdraw:
        if error == "REFUND_NOT_PENDING":
            return "该退款申请已进入处理或已结束，本次未撤回。请先查询退款进度。"
        if error == "NO_ACTIVE_REFUND":
            return "该订单当前没有可撤回的退款申请。"
        if error == "REFUND_NOT_FOUND":
            return "未找到可撤回的退款申请，本次未执行。"

    reason = result.get("reason", "当前操作无法执行。")
    if is_cancel:
        return f"本次未执行取消：{reason}"
    if is_withdraw:
        return f"本次未撤回退款申请：{reason}"
    return f"未创建新的退款申请：{reason}"


def _handle_pending_confirmation(
    active: dict,
    user_message: str,
    client,
    *,
    now: datetime | None,
    trace_id: str | None = None,
) -> str | None:
    """处理明确指令；返回 None 时，本轮继续只读问答。"""
    if active["status"] == "approved":
        return "当前线程有已批准且尚未结束的操作，请先处理该操作。"
    if active["tool"] not in {"cancel_order", "create_refund", "cancel_refund"}:
        return "无法识别当前待确认操作，本次未执行。"

    expected_intent = confirmation_command(user_message, tool=active["tool"])
    if expected_intent is None:
        return None

    intent = classify_confirmation_intent(
        client,
        user_message=user_message,
        summary=active["summary"],
    )
    emit_trace(
        "confirmation.intent",
        run_id=trace_id,
        tool=active["tool"],
        intent=intent,
    )
    if intent != expected_intent:
        return (
            "尚未识别到明确的确认或拒绝决定，当前操作继续等待。\n"
            + _confirmation_answer(active)
        )

    # 确认编号和身份来自已读取的当前会话记录，不来自模型。
    context = {
        "user_id": active["user_id"],
        "session_id": active["session_id"],
        "thread_id": active["thread_id"],
        "confirmation_id": active["confirmation_id"],
    }

    # 模型识别可能耗时，实际执行时重新取时间。
    decision_time = now if now is not None else datetime.now(UTC)

    if intent == "reject":
        result = reject_confirmation(**context, now=decision_time)
    else:
        approve = {
            "cancel_order": approve_cancel_confirmation,
            "create_refund": approve_refund_confirmation,
            "cancel_refund": approve_withdraw_confirmation,
        }[active["tool"]]
        result = approve(**context, now=decision_time)

    emit_trace(
        "confirmation.decision",
        run_id=trace_id,
        source="chat",
        tool=active["tool"],
        decision="reject" if intent == "reject" else "approve",
        status=result.get("status"),
        error=result.get("error"),
    )
    return format_confirmation_result(tool=active["tool"], result=result)


def _pending_cancel_eligibility(
    message: str, active: dict, state: ConversationState, *, now: datetime,
) -> str | None:
    """仅续答绑定于当前取消确认的资格问题。"""
    clarification = state.pending_clarification
    is_question = asks_cancel_eligibility(message)
    if not is_question and clarification is None:
        return None

    pending_id = clarification["pending_order_id"] if clarification else active["order_id"]
    queried_id = clarification["queried_order_id"] if clarification else state.last_queried_order_id
    reference = order_reference(message, choice_only=not is_question)
    if not is_question and reference not in {"pending", "queried", pending_id.upper(), queried_id.upper()}:
        # 新查询交给常规只读问答。含糊短答继续等待用户点明候选。
        if off_topic_reason(message) or any(word in message for word in (
            "查", "退款", "物流", "发货", "付款", "政策", "取消", "换个话题", "不问了",
        )):
            state.pending_clarification = None
            return None
        reference = None

    if reference is None and queried_id and queried_id != pending_id:
        state.pending_clarification = {
            "kind": "cancel_eligibility",
            "pending_order_id": pending_id,
            "queried_order_id": queried_id,
            "confirmation_id": active["confirmation_id"],
        }
        return f"你问哪笔订单能否取消？A 是正在等取消确认的 {pending_id}，B 是刚查过的 {queried_id}。"

    state.pending_clarification = None
    order_id = {"pending": pending_id, "queried": queried_id, None: pending_id}.get(reference, reference)
    if order_id is None:
        return "当前会话还没有最近查过的订单。请提供订单号。"
    if order_id == pending_id.upper():
        order_id = pending_id
    elif queried_id and order_id == queried_id.upper():
        order_id = queried_id
    result = check_cancel_eligibility(user_id=active["user_id"], order_id=order_id, now=now)
    if not result.get("ok"):
        return "未找到该订单。请核对订单号。"
    verdict = "可以取消" if result["eligible"] else "不能取消"
    return f"订单 {order_id} {verdict}。{result['reason']}"


def _pending_question_messages(
    messages: list, user_index: int, active: dict, last_queried_order_id: str | None,
) -> list:
    """仅为模型构造本轮的操作快照，原始聊天历史照常保存。"""
    model_messages = messages.copy()
    model_messages[user_index] = {
        "role": "user",
        "content": json.dumps(
            {
                "pending_action": {key: active[key] for key in ("order_id", "tool", "summary")},
                "last_queried_order_id": last_queried_order_id,
                "current_user_message": messages[user_index]["content"],
            },
            ensure_ascii=False,
        ),
    }
    if model_messages[0]["role"] == "system":
        system = model_messages[0]
        model_messages[0] = {
            **system,
            "content": f"{system['content']}\n\n{PENDING_QUESTION_PROMPT}",
        }
    else:
        model_messages.insert(0, {"role": "system", "content": PENDING_QUESTION_PROMPT})
    return model_messages


def _token_event(text: str) -> dict:
    return {"event": "token", "data": {"text": text}}


def _emit_answer(messages: list, answer: str, *, stream: bool):
    """确定性回复统一收口：写入历史，流式时再输出完整文本。

    模型直接给出的文本回复不走这里——它的 assistant 消息已经写入历史，
    流式增量 token 也已经输出，重复输出会让 done.reply 变成两遍。
    """
    messages.append({"role": "assistant", "content": answer})
    if stream:
        yield _token_event(answer)


def _automatic_tool(
    call_tool, name: str, args: dict, budget: ExecutionBudget, trace_id: str, **context,
) -> dict:
    expired = budget.expired()
    if expired or not budget.has_tool_quota():
        error = "duration" if expired else "tool_calls"
        emit_trace("budget.exceeded", run_id=trace_id, kind=error, **budget.snapshot())
        answer = TIME_BUDGET_ANSWER if error == "duration" else TOOL_BUDGET_ANSWER
        return {"ok": False, "answer": answer}
    budget.consume_tool()
    function = SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False))
    call = SimpleNamespace(function=function)
    return call_tool(call, **context)


def _cancel_target(call_tool, lookup: dict, order_id: str, budget: ExecutionBudget, trace_id: str) -> str:
    result = _automatic_tool(
        call_tool, "cancel_order", {"order_id": order_id, "reason": lookup["cancel_reason"]},
        budget, trace_id, expected_cancel_amount=lookup["paid_amount_cents"],
    )
    if result.get("status") == "awaiting_confirmation":
        return _confirmation_answer(result)
    if result.get("error") == "POLICY_DENIED":
        next_step = (
            "你可以查询物流或退款资格。"
            if result["policy_code"] in {"STATUS_NOT_SUPPORTED", "ORDER_ALREADY_FULFILLED"} else
            "你可以查询订单状态或联系售后。"
        )
        return f"订单 {order_id} 无法取消。{result['reason']}{next_step}"
    if result.get("error") == "ORDER_AMOUNT_CHANGED":
        return f"订单 {order_id} 的实付金额已变化。请核对金额后重新发起取消。"
    if result.get("error") == "ORDER_NOT_FOUND":
        return "未找到该本人订单。请核对订单号，或重新按金额查找。"
    return result.get("answer") or _tool_failure_answer(result) or "未能准备取消。请先查询订单状态。"


def _cancel_lookup_reply(
    lookup: dict, call_tool, budget: ExecutionBudget, trace_id: str,
) -> tuple[str, dict | None]:
    if not lookup.get("ok"):
        answer = (
            lookup.get("answer") or _tool_failure_answer(lookup) or "暂时无法查找订单，请核对金额后重试。"
        )
        return answer, None
    orders = lookup["orders"]
    if lookup["offset"] == 0 and len(orders) == 1 and not lookup["has_more"]:
        return _cancel_target(call_tool, lookup, orders[0]["order_id"], budget, trace_id), None
    return _order_lookup_answer(lookup), lookup if orders else None


def _continue_cancel_selection(
    message: str, lookup: dict, call_tool, budget: ExecutionBudget, trace_id: str,
) -> tuple[str, dict | None]:
    choice = message.strip().rstrip("。！!.").strip()
    if choice in {"算了", "不取消了", "放弃", "放弃取消"}:
        return "已退出本次订单选择。", None
    if order_id := selected_order(choice, lookup["orders"]):
        return _cancel_target(call_tool, lookup, order_id, budget, trace_id), None
    if any(phrase in choice for phrase in ("取消政策", "取消规则", "取消订单的政策", "取消订单的规则")):
        result = _automatic_tool(call_tool, "get_policy", {"topic": "cancellation"}, budget, trace_id)
        if result.get("ok"):
            return f"{result['policy']}\n可以继续回复当前页的订单号或序号。", lookup
        return result.get("answer") or _tool_failure_answer(result) or "暂时无法查询取消政策。", lookup
    if choice == "看更多" and lookup["has_more"]:
        next_page = _automatic_tool(
            call_tool, "find_orders_by_amount", {
                "paid_amount_cents": lookup["paid_amount_cents"], "offset": lookup["next_offset"],
                "purpose": "cancel_order", "reason": lookup["cancel_reason"],
            }, budget, trace_id,
        )
        if next_page.get("ok") and next_page["orders"]:
            return _order_lookup_answer(next_page), next_page
        if next_page.get("ok"):
            return "没有更多候选。\n" + _order_lookup_answer(lookup), lookup
        answer = next_page.get("answer") or _tool_failure_answer(next_page) or "暂时无法查看下一页。"
        return answer, lookup
    return _order_lookup_answer(lookup), lookup


def _execute(
    user_message: str,
    user_id: str,
    messages: list,
    client,
    max_steps: int,
    *,
    session_id: str | None,
    thread_id: str | None,
    now: datetime | None,
    trace_id: str,
    budget: ExecutionBudget,
    state: ConversationState,
    stream: bool,
) -> Generator[dict, None, str]:
    """唯一的执行内核；``stream`` 只决定输出通道，不改变任何执行规则。"""
    user_index = len(messages)
    messages.append({"role": "user", "content": user_message})

    current_time = now if now is not None else datetime.now(UTC)
    has_context = all(
        isinstance(value, str) and value.strip()
        for value in (user_id, session_id, thread_id)
    )
    active = None
    if has_context:
        active = load_active_confirmation(
            user_id=user_id, session_id=session_id, thread_id=thread_id, now=current_time,
        )
    state.discard_finished_clarification(active)

    if active is not None:
        state.cancel_selection = None
        if (
            active["tool"] == "cancel_order" and state.last_queried_order_id
            and state.last_queried_order_id != active["order_id"]
            and confirmation_command(user_message, tool="cancel_order") == "approve"
        ):
            answer = f"请点击订单 {active['order_id']} 的确认卡执行取消。"
        else:
            answer = _handle_pending_confirmation(active, user_message, client, now=now, trace_id=trace_id)
        if answer is None and active["tool"] == "cancel_order" and active["status"] == "pending":
            answer = _pending_cancel_eligibility(user_message, active, state, now=current_time)
        if answer is not None:
            yield from _emit_answer(messages, answer, stream=stream)
            return answer

    pending_question = active is not None
    call_tool = partial(
        execute_tool, user_id=user_id, session_id=session_id, thread_id=thread_id,
        now=current_time, trace_id=trace_id, pending_question=pending_question,
    )
    if state.cancel_selection is not None:
        answer, state.cancel_selection = _continue_cancel_selection(
            user_message, state.cancel_selection, call_tool, budget, trace_id,
        )
        yield from _emit_answer(messages, answer, stream=stream)
        return answer

    if not pending_question and has_context and confirmation_command(user_message) is not None:
        answer = "当前没有有效的待确认操作。请重新发起需要处理的操作。"
        yield from _emit_answer(messages, answer, stream=stream)
        return answer

    # 域外请求在这里终止，不进入模型循环，也不消耗工具配额。
    scope_reason = off_topic_reason(user_message)
    if scope_reason is not None:
        emit_trace(
            "scope.blocked",
            run_id=trace_id,
            reason=scope_reason,
            message_chars=len(user_message),
        )
        yield from _emit_answer(messages, SCOPE_REFUSAL, stream=stream)
        return SCOPE_REFUSAL

    amount_cents = None if pending_question else cancel_amount_cents(user_message)
    if amount_cents is not None:
        lookup = _automatic_tool(
            call_tool, "find_orders_by_amount",
            {"paid_amount_cents": amount_cents, "purpose": "cancel_order"}, budget, trace_id,
        )
        answer, state.cancel_selection = _cancel_lookup_reply(lookup, call_tool, budget, trace_id)
        yield from _emit_answer(messages, answer, stream=stream)
        return answer

    logistics_order_id = None
    looked_up_order = False
    cancel_needs_lookup = not pending_question and requires_cancel_lookup(user_message)
    exposed_tools = PENDING_QUESTION_TOOL_SCHEMAS if pending_question else TOOLS
    buffer_tool_turns = pending_question or cancel_needs_lookup
    for step in range(max_steps):
        if budget.expired():
            answer = TIME_BUDGET_ANSWER
            emit_trace(
                "budget.exceeded",
                run_id=trace_id,
                step=step,
                kind="duration",
                **budget.snapshot(),
            )
            yield from _emit_answer(messages, answer, stream=stream)
            return answer

        emit_trace("model.start", run_id=trace_id, step=step, message_count=len(messages))
        model_started = monotonic()
        model_status = "ok"
        message = None
        streamed_parts: list[str] = []
        try:
            model_messages = (
                _pending_question_messages(messages, user_index, active, state.last_queried_order_id)
                if active is not None else messages
            )
            window, context_stats = trim_messages(model_messages)
            if context_stats["dropped"] or context_stats["truncated"]:
                emit_trace(
                    "context.trimmed",
                    run_id=trace_id,
                    step=step,
                    **context_stats,
                )
            if stream:
                for kind, value in stream_model(client, window, exposed_tools):
                    if kind == "token":
                        streamed_parts.append(value)
                        if not buffer_tool_turns:
                            yield _token_event(value)
                    elif kind == "message":
                        message = value
            else:
                message = request_model(client, window, exposed_tools)
        except GeneratorExit:
            model_status = "cancelled"
            raise
        except Exception:
            model_status = "error"
            raise
        finally:
            emit_trace(
                "model.end",
                run_id=trace_id,
                step=step,
                status=model_status,
                duration_ms=round((monotonic() - model_started) * 1000, 2),
                tool_call_count=len(message.tool_calls or []) if message else 0,
                token_chars=sum(len(part) for part in streamed_parts),
            )

        if stream and message is None:
            raise RuntimeError("流式模型没有返回完整消息")

        # 等待确认或按金额取消时，仅显示没有工具调用的模型回复。
        if stream and (not buffer_tool_turns or not message.tool_calls):
            if buffer_tool_turns:
                for part in streamed_parts:
                    yield _token_event(part)
            # 兼容只返回完整消息、没有增量文本的模型替身。
            if message.content and not streamed_parts:
                yield _token_event(message.content)

        assistant_message = message.model_dump(exclude_none=True)
        if pending_question and message.tool_calls:
            assistant_message.pop("content", None)
        messages.append(assistant_message)

        if not message.tool_calls:
            answer = message.content or "模型没有返回文本。"
            if stream and not message.content and not streamed_parts:
                yield _token_event(answer)
            return answer

        awaiting = None
        order_lookup_answer = None
        cancel_lookup = None
        tool_results = []
        exhausted = False
        looking_up_order = not pending_question and any(
            call.function.name == "find_orders_by_amount" for call in message.tool_calls
        )
        looked_up_order = looked_up_order or looking_up_order
        for tool_call in message.tool_calls:
            if awaiting is not None:
                # 给每个 tool_call 补齐结果，但不继续执行同一批的后续动作。
                result = {"ok": False, "error": "AWAITING_CONFIRMATION"}
            elif order_lookup_answer is not None or cancel_lookup is not None:
                result = {"ok": False, "error": "ORDER_SELECTION_REQUIRED"}
            elif (
                (looking_up_order and tool_call.function.name == "get_logistics")
                or ((cancel_needs_lookup or looked_up_order) and tool_call.function.name == "cancel_order")
            ):
                result = {"ok": False, "error": "ORDER_LOOKUP_REQUIRED"}
            elif not budget.has_tool_quota():
                # 超预算也要补齐结果，否则消息序列不合法，下一轮直接 400。
                result = {"ok": False, "error": "BUDGET_EXCEEDED"}
                exhausted = True
            else:
                budget.consume_tool()
                result = call_tool(tool_call, logistics_order_id=logistics_order_id)
                if result.get("ok"):
                    name = tool_call.function.name
                    if name == "get_order" and (order_id := result.get("order", {}).get("order_id")):
                        state.last_queried_order_id = order_id
                    elif name in {"get_logistics", "get_refund_status", "check_refund_eligibility"} and (
                        order_id := result.get("order_id")
                    ):
                        state.last_queried_order_id = order_id
                if result.get("status") == "awaiting_confirmation":
                    awaiting = result
                if tool_call.function.name == "find_orders_by_amount" and result.get("ok"):
                    orders = result["orders"]
                    if "cancel_reason" in result:
                        cancel_lookup = result
                    elif result["offset"] == 0 and len(orders) == 1 and not result["has_more"]:
                        logistics_order_id = orders[0]["order_id"]
                    else:
                        order_lookup_answer = _order_lookup_answer(result)
            tool_results.append(result)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

        if awaiting is not None:
            answer = _confirmation_answer(awaiting)
            yield from _emit_answer(messages, answer, stream=stream)
            return answer

        if cancel_lookup is not None:
            answer, state.cancel_selection = _cancel_lookup_reply(cancel_lookup, call_tool, budget, trace_id)
            yield from _emit_answer(messages, answer, stream=stream)
            return answer

        if order_lookup_answer is not None:
            yield from _emit_answer(messages, order_lookup_answer, stream=stream)
            return order_lookup_answer

        if exhausted:
            answer = TOOL_BUDGET_ANSWER
            emit_trace(
                "budget.exceeded",
                run_id=trace_id,
                step=step,
                kind="tool_calls",
                **budget.snapshot(),
            )
            yield from _emit_answer(messages, answer, stream=stream)
            return answer

        if pending_question and all(result.get("error") == PENDING_TOOL_NOT_ALLOWED for result in tool_results):
            answer = "你可以继续查询订单或政策。要发起新的操作，请先确认或放弃当前操作。"
            yield from _emit_answer(messages, answer, stream=stream)
            return answer

        failure_answer = _single_tool_failure_answer(message.tool_calls, tool_results)
        if failure_answer is not None:
            yield from _emit_answer(messages, failure_answer, stream=stream)
            return failure_answer

    answer = "已达到本次执行的步数上限，请稍后重试。"
    yield from _emit_answer(messages, answer, stream=stream)
    return answer


def run_agent(
    user_message: str,
    user_id: str,
    messages: list,
    client,
    max_steps: int = 4,
    *,
    session_id: str | None = None,
    thread_id: str | None = None,
    now: datetime | None = None,
    trace_id: str | None = None,
    budget: ExecutionBudget | None = None,
    state: ConversationState | None = None,
) -> str:
    """非流式入口：驱动同一个内核，取回最终回复。"""
    if max_steps < 1:
        raise ValueError("max_steps 必须至少为 1")
    loop = _execute(
        user_message,
        user_id,
        messages,
        client,
        max_steps,
        session_id=session_id,
        thread_id=thread_id,
        now=now,
        trace_id=trace_id or new_run_id(),
        budget=budget or ExecutionBudget(),
        state=state if state is not None else ConversationState(),
        stream=False,
    )
    try:
        while True:
            # 非流式内核不产出对外事件；驱动它执行完并接收 return 值。
            next(loop)
    except StopIteration as finished:
        answer = finished.value
        if answer is None:
            raise RuntimeError("执行内核没有返回回复") from None
        return answer


def stream_agent(
    user_message: str,
    user_id: str,
    messages: list,
    client,
    max_steps: int = 4,
    *,
    session_id: str | None = None,
    thread_id: str | None = None,
    now: datetime | None = None,
    trace_id: str | None = None,
    budget: ExecutionBudget | None = None,
    state: ConversationState | None = None,
):
    """流式入口：把同一个内核产出的 token 事件实时透传。"""
    if max_steps < 1:
        raise ValueError("max_steps 必须至少为 1")
    yield from _execute(
        user_message,
        user_id,
        messages,
        client,
        max_steps,
        session_id=session_id,
        thread_id=thread_id,
        now=now,
        trace_id=trace_id or new_run_id(),
        budget=budget or ExecutionBudget(),
        state=state if state is not None else ConversationState(),
        stream=True,
    )
