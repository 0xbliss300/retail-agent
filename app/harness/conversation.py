"""对话编排：HTTP 入口使用的会话打开、消息处理和保存。"""

import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from openai import APIError

from app.agent.prompts import (
    CANCELLATION_PROMPT,
    ORDER_PROMPT,
    POLICY_PROMPT,
    REFUND_PROMPT,
    build_system_prompt,
)
from app.harness.confirmation import load_active_confirmation
from app.harness.runtime import create_messages, run_agent, stream_agent
from app.storage.persistence import ConversationConflict, load_conversation, save_conversation

SYSTEM_PROMPT = build_system_prompt(
    ORDER_PROMPT,
    POLICY_PROMPT,
    REFUND_PROMPT,
    CANCELLATION_PROMPT,
)

HISTORY_CONFLICT_WARNING = (
    "本轮历史未保存：对话已被其他窗口更新。"
    "已执行的业务操作不会因此撤销，请先查询订单或退款状态，不要重复提交。"
)

HISTORY_FAILURE_WARNING = (
    "本轮历史未保存。"
    "已执行的业务操作不会因此撤销，请先查询订单或退款状态，不要重复提交。"
)


def _current_time(now: datetime | None) -> datetime:
    current = now if now is not None else datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now 必须包含时区")
    return current


def new_conversation(*, user_id: str) -> dict:
    """创建尚未保存的新对话上下文。"""
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("user_id 不能为空")
    return {
        "user_id": user_id,
        "session_id": f"sess_{uuid4().hex}",
        "thread_id": f"thread_{uuid4().hex}",
        "messages": create_messages(SYSTEM_PROMPT),
        "version": 0,
        "resumed": False,
        "active": None,
    }


def open_conversation(
    *,
    user_id: str,
    session_id: str | None = None,
    thread_id: str | None = None,
    now: datetime | None = None,
) -> dict | None:
    """打开新对话或恢复已有对话；不存在且无待确认操作时返回 None。"""
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("user_id 不能为空")
    current = _current_time(now)

    if session_id is None and thread_id is None:
        return new_conversation(user_id=user_id)
    if (
        not isinstance(session_id, str) or not session_id.strip()
        or not isinstance(thread_id, str) or not thread_id.strip()
    ):
        raise ValueError("session_id 和 thread_id 必须同时提供")

    session_id = session_id.strip()
    thread_id = thread_id.strip()
    snapshot = load_conversation(
        user_id=user_id,
        session_id=session_id,
        thread_id=thread_id,
        now=current,
    )
    active = load_active_confirmation(
        user_id=user_id,
        session_id=session_id,
        thread_id=thread_id,
        now=current,
    )
    if snapshot is None and active is None:
        return None

    # 恢复历史时保留工具调用与工具结果，只替换系统提示词。
    messages = create_messages(SYSTEM_PROMPT)
    if snapshot is not None:
        messages.extend(snapshot["messages"][1:])

    return {
        "user_id": user_id,
        "session_id": session_id,
        "thread_id": thread_id,
        "messages": messages,
        "version": snapshot["version"] if snapshot is not None else 0,
        "resumed": snapshot is not None,
        "active": active,
    }


def pending_action(active: dict | None) -> dict | None:
    """只返回客户端需要的待确认信息，不暴露用户和会话绑定字段。"""
    if active is None:
        return None
    return {
        "confirmation_id": active["confirmation_id"],
        "tool": active["tool"],
        "order_id": active["order_id"],
        "summary": active["summary"],
        "status": active["status"],
        "expires_at": active["expires_at"],
    }


def send_message(
    *,
    context: dict,
    message: str,
    client,
    now: datetime | None = None,
) -> dict:
    """执行一轮对话并保存完整历史；保存失败不重试业务操作。"""
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message 不能为空")
    current = _current_time(now)

    answer = run_agent(
        message,
        context["user_id"],
        context["messages"],
        client,
        session_id=context["session_id"],
        thread_id=context["thread_id"],
        now=current,
    )

    history_saved = True
    warning = None
    try:
        context["version"] = save_conversation(
            user_id=context["user_id"],
            session_id=context["session_id"],
            thread_id=context["thread_id"],
            messages=context["messages"],
            now=current,
            expected_version=context["version"],
        )
    except ConversationConflict:
        history_saved = False
        warning = HISTORY_CONFLICT_WARNING
    except (sqlite3.Error, ValueError, TypeError):
        history_saved = False
        warning = HISTORY_FAILURE_WARNING

    active = load_active_confirmation(
        user_id=context["user_id"],
        session_id=context["session_id"],
        thread_id=context["thread_id"],
        now=current,
    )
    context["active"] = active

    return {
        "reply": answer,
        "session_id": context["session_id"],
        "thread_id": context["thread_id"],
        "pending_action": pending_action(active),
        "history_saved": history_saved,
        "warning": warning,
    }


def stream_message(
    *,
    context: dict,
    message: str,
    client,
    now: datetime | None = None,
):
    """流式执行一轮对话：文本 token 实时产出，流结束后保存历史。"""
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message 不能为空")
    current = _current_time(now)

    reply_parts: list[str] = []
    try:
        for event in stream_agent(
            message,
            context["user_id"],
            context["messages"],
            client,
            session_id=context["session_id"],
            thread_id=context["thread_id"],
            now=current,
        ):
            if event.get("event") == "token":
                reply_parts.append(event["data"]["text"])
            yield event
    except APIError:
        yield {
            "event": "error",
            "data": {
                "error": "MODEL_ERROR",
                "message": "模型服务暂时不可用，请稍后重试。",
            },
        }
        return

    history_saved = True
    warning = None
    try:
        context["version"] = save_conversation(
            user_id=context["user_id"],
            session_id=context["session_id"],
            thread_id=context["thread_id"],
            messages=context["messages"],
            now=current,
            expected_version=context["version"],
        )
    except ConversationConflict:
        history_saved = False
        warning = HISTORY_CONFLICT_WARNING
    except (sqlite3.Error, ValueError, TypeError):
        history_saved = False
        warning = HISTORY_FAILURE_WARNING

    active = load_active_confirmation(
        user_id=context["user_id"],
        session_id=context["session_id"],
        thread_id=context["thread_id"],
        now=current,
    )
    context["active"] = active

    yield {
        "event": "done",
        "data": {
            "reply": "".join(reply_parts),
            "session_id": context["session_id"],
            "thread_id": context["thread_id"],
            "pending_action": pending_action(active),
            "history_saved": history_saved,
            "warning": warning,
        },
    }