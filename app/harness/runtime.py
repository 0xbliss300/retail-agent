"""会话消息与模型、工具之间的执行循环。"""

import json
from datetime import datetime, timezone

from app.agent.confirmation_intent import classify_confirmation_intent
from app.business.service import approve_refund_confirmation
from app.agent.model_client import request_model
from app.business.tools import TOOLS
from app.harness.confirmation import load_active_confirmation, reject_confirmation
from app.harness.tool_executor import execute_tool

def create_messages(system_prompt: str) -> list:
    """每段新对话创建独立的消息列表。"""
    return [{"role": "system", "content": system_prompt}]


def _confirmation_answer(record: dict) -> str:
    return (
        f"{record['summary']}\n"
        f"确认编号：{record['confirmation_id']}\n"
        f"有效期至：{record['expires_at']}\n"
        "当前尚未创建退款申请。"
        "你可以说“确认提交”，或“算了，不申请了”。"
    )

# 处理操作
def _handle_pending_confirmation(
    active: dict,
    user_message: str,
    client,
    *,
    now: datetime | None,
) -> str:
    if active["status"] == "approved":
        return "当前线程有已批准且尚未结束的操作，请先处理该操作。"

    intent = classify_confirmation_intent(
        client,
        user_message=user_message,
        summary=active["summary"],
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
    decision_time = now if now is not None else datetime.now(timezone.utc)

    if intent == "reject":
        result = reject_confirmation(**context, now=decision_time)
        if result.get("status") == "rejected":
            return "已拒绝当前操作，未创建退款申请。"
    else:
        result = approve_refund_confirmation(**context, now=decision_time)
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
        return "当前操作已过期，未创建退款申请。请重新发起申请。"
    if error == "CONFIRMATION_STALE":
        return (
            "订单金额已变化，未提交退款申请（CONFIRMATION_STALE）。"
            "请先拒绝当前操作，再重新发起申请以核对新金额。"
        )

    reason = result.get("reason", "当前操作无法执行。")
    return f"未创建新的退款申请（{error}）：{reason}"

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
) -> str:
    """处理一条用户消息，并将本轮对话追加到已有历史。"""
    if max_steps < 1:
        raise ValueError("max_steps 必须至少为 1")

    messages.append({"role": "user", "content": user_message})

    current_time = now if now is not None else datetime.now(timezone.utc)
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
        )
        messages.append({"role": "assistant", "content": answer})
        return answer

    for _ in range(max_steps):
        message = request_model(client, messages, TOOLS)
        messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            return message.content or "模型没有返回文本。"

        awaiting = None
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
                )
                if result.get("status") == "awaiting_confirmation":
                    awaiting = result
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

    answer = "已达到本次执行的步数上限，请稍后重试。"
    messages.append({"role": "assistant", "content": answer})
    return answer
