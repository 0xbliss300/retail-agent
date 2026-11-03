"""会话消息与模型、工具之间的执行循环。"""

import json
from datetime import UTC, datetime
from time import monotonic

from app.agent.confirmation_intent import classify_confirmation_intent
from app.agent.model_client import request_model, stream_model
from app.business.service import approve_cancel_confirmation, approve_refund_confirmation
from app.business.tools import TOOLS
from app.harness.confirmation import load_active_confirmation, reject_confirmation
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
    return (
        f"{record['summary']}\n"
        f"确认编号：{record['confirmation_id']}\n"
        f"有效期至：{record['expires_at']}\n"
        "当前尚未创建退款申请。"
        "你可以说“确认提交”，或“算了，不申请了”。"
    )

def format_confirmation_result(*, is_cancel: bool, result: dict) -> str:
    """把批准或拒绝的服务端结果转换为用户可读回复。"""
    if result.get("status") == "rejected":
        return "已拒绝本次取消请求，本次未修改订单。" if is_cancel else "已拒绝当前操作，未创建退款申请。"

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
        return "当前操作已过期，未创建退款申请。请重新发起申请。"
    if error == "CONFIRMATION_STALE":
        if is_cancel:
            return (
                "订单状态或付款信息已变化，本次未执行取消（CONFIRMATION_STALE）。"
                "请先拒绝当前操作，再重新发起取消以核对新摘要。"
            )
        return (
            "订单金额已变化，未提交退款申请（CONFIRMATION_STALE）。"
            "请先拒绝当前操作，再重新发起申请以核对新金额。"
        )

    reason = result.get("reason", "当前操作无法执行。")
    if is_cancel:
        return f"本次未执行取消（{error}）：{reason}"
    return f"未创建新的退款申请（{error}）：{reason}"


# 处理操作
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
    is_cancel = active["tool"] == "cancel_order"
    if active["tool"] not in {"cancel_order", "create_refund"}:
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
        approve = approve_cancel_confirmation if is_cancel else approve_refund_confirmation
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
    return format_confirmation_result(is_cancel=is_cancel, result=result)

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
) -> str:
    """处理一条用户消息，并将本轮对话追加到已有历史。"""
    if max_steps < 1:
        raise ValueError("max_steps 必须至少为 1")

    trace_id = trace_id or new_run_id()
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
        messages.append({"role": "assistant", "content": answer})
        return answer

    for step in range(max_steps):
        emit_trace("model.start", run_id=trace_id, step=step, message_count=len(messages))
        model_started = monotonic()
        model_status = "ok"
        message = None
        try:
            message = request_model(client, messages, TOOLS)
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
            )
        messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            return message.content or "模型没有返回文本。"

        awaiting = None
        tool_results = []
        for tool_call in message.tool_calls:
            if awaiting is not None:
                # 给每个 tool_call 补齐结果，但不继续执行同一批的后续动作。
                result = {"ok": False, "error": "AWAITING_CONFIRMATION"}
            else:
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
            messages.append({"role": "assistant", "content": answer})
            return answer

        failure_answer = _single_tool_failure_answer(message.tool_calls, tool_results)
        if failure_answer is not None:
            messages.append({"role": "assistant", "content": failure_answer})
            return failure_answer

    answer = "已达到本次执行的步数上限，请稍后重试。"
    messages.append({"role": "assistant", "content": answer})
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
):
    """流式执行一轮对话：文本增量输出，工具调用完整缓冲后执行。"""
    if max_steps < 1:
        raise ValueError("max_steps 必须至少为 1")

    trace_id = trace_id or new_run_id()
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
        messages.append({"role": "assistant", "content": answer})
        yield {"event": "token", "data": {"text": answer}}
        return

    for step in range(max_steps):
        emit_trace("model.start", run_id=trace_id, step=step, message_count=len(messages))
        streamed_parts: list[str] = []
        message = None
        model_started = monotonic()
        model_status = "ok"
        try:
            for kind, value in stream_model(client, messages, TOOLS):
                if kind == "token":
                    streamed_parts.append(value)
                    yield {"event": "token", "data": {"text": value}}
                elif kind == "message":
                    message = value
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

        if message is None:
            raise RuntimeError("流式模型没有返回完整消息")

        # 兼容只返回完整消息、没有增量文本的模型替身。
        if message.content and not streamed_parts:
            yield {"event": "token", "data": {"text": message.content}}

        messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            if not message.content and not streamed_parts:
                answer = "模型没有返回文本。"
                yield {"event": "token", "data": {"text": answer}}
            return

        awaiting = None
        tool_results = []
        for tool_call in message.tool_calls:
            if awaiting is not None:
                result = {"ok": False, "error": "AWAITING_CONFIRMATION"}
            else:
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
            messages.append({"role": "assistant", "content": answer})
            yield {"event": "token", "data": {"text": answer}}
            return

        failure_answer = _single_tool_failure_answer(message.tool_calls, tool_results)
        if failure_answer is not None:
            messages.append({"role": "assistant", "content": failure_answer})
            yield {"event": "token", "data": {"text": failure_answer}}
            return

    answer = "已达到本次执行的步数上限，请稍后重试。"
    messages.append({"role": "assistant", "content": answer})
    yield {"event": "token", "data": {"text": answer}}
