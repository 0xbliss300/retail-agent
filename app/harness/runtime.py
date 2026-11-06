"""会话消息与模型、工具之间的执行循环。

``_execute`` 是唯一的执行内核：确认拦截、域外闸门、预算、上下文裁剪、
模型调用、工具消息配对、确定性回复和 Trace 都只在这里实现一次。
``run_agent`` 与 ``stream_agent`` 只是同一个内核的两种输出通道，
流式负责实时吐出 token，非流式负责取回最终回复。
"""

import json
from collections.abc import Generator
from datetime import UTC, datetime
from time import monotonic

from app.agent.confirmation_intent import classify_confirmation_intent
from app.agent.model_client import request_model, stream_model
from app.agent.scope import SCOPE_REFUSAL, off_topic_reason
from app.business.service import (
    approve_cancel_confirmation,
    approve_refund_confirmation,
    approve_withdraw_confirmation,
)
from app.business.tools import TOOLS
from app.harness.budget import (
    TIME_BUDGET_ANSWER,
    TOOL_BUDGET_ANSWER,
    ExecutionBudget,
)
from app.harness.confirmation import load_active_confirmation, reject_confirmation
from app.harness.context import trim_messages
from app.harness.tool_executor import execute_tool
from app.observability.tracing import emit_trace, new_run_id


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
}


def _tool_failure_answer(result: dict) -> str | None:
    """只处理工具执行边界错误；业务拒绝仍交给模型解释。"""
    return TOOL_FAILURE_ANSWERS.get(result.get("error"))


def _single_tool_failure_answer(tool_calls: list, tool_results: list) -> str | None:
    """单个工具失败时直接给出确定性回复，避免模型编造成功结果。"""
    if len(tool_calls) != 1 or len(tool_results) != 1:
        return None
    return _tool_failure_answer(tool_results[0])


def _confirmation_answer(record: dict) -> str:
    if record.get("tool") == "cancel_order":
        return (
            f"{record['summary']}\n确认编号：{record['confirmation_id']}\n"
            f"有效期至：{record['expires_at']}\n"
            "本次取消尚未执行。你可以说“确认取消”，或“算了，不取消了”。"
        )
    if record.get("tool") == "cancel_refund":
        return (
            f"{record['summary']}\n确认编号：{record['confirmation_id']}\n"
            f"有效期至：{record['expires_at']}\n"
            "当前退款申请尚未撤回。你可以说“确认撤回”，或“算了，不撤了」。"
        )
    return (
        f"{record['summary']}\n"
        f"确认编号：{record['confirmation_id']}\n"
        f"有效期至：{record['expires_at']}\n"
        "当前尚未创建退款申请。"
        "你可以说“确认提交”，或“算了，不申请了”。"
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
        answer = f"订单 {result['order_id']} 已取消（canceled）。"
        refund = result["refund"]
        if refund is None:
            return answer + "未付款订单无需退款，未生成退款流水。"
        amount = refund["amount_cents"]
        return (
            answer + f"\n模拟取消退款流水：{refund['refund_id']}\n"
            f"金额：{amount // 100}.{amount % 100:02d}元，流水状态：completed。"
            "这是本地模拟结果，未发生真实资金划转。"
        )

    if is_withdraw and result.get("ok") and result.get("status") == "withdrawn":
        amount = result["amount_cents"]
        return (
            f"订单 {result['order_id']} 的退款申请 {result['refund_id']} 已撤回（withdrawn）。\n"
            f"申请金额：{amount // 100}.{amount % 100:02d} 元\n"
            "订单状态未改变；如需退款，可以重新提交申请。"
        )

    if result.get("ok") and result.get("status") == "submitted":
        amount = result["amount_cents"]
        return (
            f"订单 {result['order_id']} 的退款申请已提交。\n"
            f"申请编号：{result['refund_id']}\n"
            f"申请金额：{amount // 100}.{amount % 100:02d} 元\n"
            "申请状态：pending（待处理）。提交申请不代表退款已到账。"
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
                "订单状态或付款信息已变化，本次未执行取消（CONFIRMATION_STALE）。"
                "请先拒绝当前操作，再重新发起取消以核对新摘要。"
            )
        if is_withdraw:
            return (
                "退款申请金额已变化，本次未执行撤回（CONFIRMATION_STALE）。"
                "请先拒绝当前操作，再重新发起撤回以核对新金额。"
            )
        return (
            "订单金额已变化，未提交退款申请（CONFIRMATION_STALE）。"
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
        return f"本次未执行取消（{error}）：{reason}"
    if is_withdraw:
        return f"本次未撤回退款申请（{error}）：{reason}"
    return f"未创建新的退款申请（{error}）：{reason}"


def _handle_pending_confirmation(
    active: dict,
    user_message: str,
    client,
    *,
    now: datetime | None,
    trace_id: str | None = None,
) -> str:
    if active["status"] == "approved":
        return "当前线程有已批准且尚未结束的操作，请先处理该操作。"
    if active["tool"] not in {"cancel_order", "create_refund", "cancel_refund"}:
        return "无法识别当前待确认操作，本次未执行。"

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
    if intent == "other":
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
    stream: bool,
) -> Generator[dict, None, str]:
    """唯一的执行内核；``stream`` 只决定输出通道，不改变任何执行规则。"""
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

    if active is not None:
        answer = _handle_pending_confirmation(
            active,
            user_message,
            client,
            now=now,
            trace_id=trace_id,
        )
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
            window, context_stats = trim_messages(messages)
            if context_stats["dropped"] or context_stats["truncated"]:
                emit_trace(
                    "context.trimmed",
                    run_id=trace_id,
                    step=step,
                    **context_stats,
                )
            if stream:
                for kind, value in stream_model(client, window, TOOLS):
                    if kind == "token":
                        streamed_parts.append(value)
                        yield _token_event(value)
                    elif kind == "message":
                        message = value
            else:
                message = request_model(client, window, TOOLS)
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

        # 兼容只返回完整消息、没有增量文本的模型替身。
        if stream and message.content and not streamed_parts:
            yield _token_event(message.content)

        messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            answer = message.content or "模型没有返回文本。"
            if stream and not message.content and not streamed_parts:
                yield _token_event(answer)
            return answer

        awaiting = None
        tool_results = []
        exhausted = False
        for tool_call in message.tool_calls:
            if awaiting is not None:
                # 给每个 tool_call 补齐结果，但不继续执行同一批的后续动作。
                result = {"ok": False, "error": "AWAITING_CONFIRMATION"}
            elif not budget.has_tool_quota():
                # 超预算也要补齐结果，否则消息序列不合法，下一轮直接 400。
                result = {"ok": False, "error": "BUDGET_EXCEEDED"}
                exhausted = True
            else:
                budget.consume_tool()
                result = execute_tool(
                    tool_call,
                    user_id,
                    session_id=session_id,
                    thread_id=thread_id,
                    now=current_time,
                    trace_id=trace_id,
                )
                if result.get("status") == "awaiting_confirmation":
                    awaiting = result
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
        stream=True,
    )
