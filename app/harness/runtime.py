"""会话消息与模型、工具之间的执行循环。"""

import json
from datetime import datetime, timezone

from app.agent.confirmation_intent import user_rejects_pending
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
        "如果不想继续，可以告诉我，例如：算了，不申请了。"
        "当前尚不支持批准提交，退款申请尚未创建。"
    )


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
        if active["status"] == "approved":
            answer = "当前线程有已批准且尚未结束的操作，请先处理该操作。"
        elif user_rejects_pending(client, user_message=user_message, order_id=active["order_id"]):
            # 模型只报告语义；要处理的记录始终来自可信上下文及数据库。
            result = reject_confirmation(
                user_id=user_id, session_id=session_id, thread_id=thread_id,
                confirmation_id=active["confirmation_id"],
                now=now if now is not None else datetime.now(timezone.utc),
            )
            if result.get("status") == "rejected":
                answer = "已拒绝当前操作，未创建退款申请。"
            elif result.get("error") == "CONFIRMATION_EXPIRED":
                answer = "当前待确认操作已过期，未创建退款申请。"
            else:
                answer = f"无法拒绝该操作：{result['error']}。未创建新的退款申请。"
        else:
            answer = (
                "还没有识别到明确的拒绝决定，当前操作继续等待。"
                "如不想继续，可以说：我不申请退款了。\n"
                + _confirmation_answer(active)
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
