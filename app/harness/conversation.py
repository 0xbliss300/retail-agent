"""对话编排：HTTP 入口使用的会话打开、消息处理和保存。"""

import sqlite3
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

from openai import APIError

from app.agent.prompts import SYSTEM_PROMPT
from app.business.service import (
    approve_cancel_confirmation,
    approve_refund_confirmation,
    approve_withdraw_confirmation,
    get_confirmation,
)
from app.harness.confirmation import load_active_confirmation, reject_confirmation
from app.harness.runtime import (
    create_messages,
    format_confirmation_result,
    run_agent,
    stream_agent,
)
from app.observability.tracing import emit_trace, new_run_id
from app.storage.persistence import ConversationConflict, load_conversation, save_conversation

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


def _save_context(context: dict, current: datetime) -> tuple[bool, str | None]:
    try:
        context["version"] = save_conversation(
            user_id=context["user_id"],
            session_id=context["session_id"],
            thread_id=context["thread_id"],
            messages=context["messages"],
            now=current,
            expected_version=context["version"],
        )
        return True, None
    except ConversationConflict:
        return False, HISTORY_CONFLICT_WARNING
    except (sqlite3.Error, ValueError, TypeError):
        return False, HISTORY_FAILURE_WARNING


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

    run_id = new_run_id()
    started = monotonic()
    emit_trace(
        "agent.start",
        run_id=run_id,
        source="chat",
        session_id=context["session_id"],
        thread_id=context["thread_id"],
        message_chars=len(message),
    )
    answer = ""
    history_saved = False
    warning = None
    active = None
    status = "ok"
    try:
        answer = run_agent(
            message,
            context["user_id"],
            context["messages"],
            client,
            session_id=context["session_id"],
            thread_id=context["thread_id"],
            now=current,
            trace_id=run_id,
        )

        history_saved, warning = _save_context(context, current)

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
    except Exception:
        status = "error"
        raise
    finally:
        emit_trace(
            "agent.end",
            run_id=run_id,
            source="chat",
            status=status,
            duration_ms=round((monotonic() - started) * 1000, 2),
            reply_chars=len(answer),
            history_saved=history_saved,
            pending=active is not None,
        )


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

    run_id = new_run_id()
    started = monotonic()
    emit_trace(
        "agent.start",
        run_id=run_id,
        source="stream",
        session_id=context["session_id"],
        thread_id=context["thread_id"],
        message_chars=len(message),
    )
    reply_parts: list[str] = []
    history_saved = False
    warning = None
    active = None
    status = "ok"
    try:
        try:
            for event in stream_agent(
                message,
                context["user_id"],
                context["messages"],
                client,
                session_id=context["session_id"],
                thread_id=context["thread_id"],
                now=current,
                trace_id=run_id,
            ):
                if event.get("event") == "token":
                    reply_parts.append(event["data"]["text"])
                yield event
        except APIError:
            status = "model_error"
            yield {
                "event": "error",
                "data": {
                    "error": "MODEL_ERROR",
                    "message": "模型服务暂时不可用，请稍后重试。",
                },
            }
            return

        history_saved, warning = _save_context(context, current)

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
    except GeneratorExit:
        status = "cancelled"
        raise
    except Exception:
        status = "error"
        raise
    finally:
        emit_trace(
            "agent.end",
            run_id=run_id,
            source="stream",
            status=status,
            duration_ms=round((monotonic() - started) * 1000, 2),
            reply_chars=len("".join(reply_parts)),
            history_saved=history_saved,
            pending=active is not None,
        )


def decide_confirmation(
    *,
    context: dict,
    confirmation_id: str,
    decision: str,
    now: datetime | None = None,
) -> dict:
    """通过显式决定批准或拒绝待确认操作；不调用模型。"""
    if (
        decision not in {"approve", "reject"}
        or not isinstance(confirmation_id, str)
        or not confirmation_id.strip()
    ):
        return {"error": "INVALID_ARGUMENTS"}

    current = _current_time(now)
    confirmation_id = confirmation_id.strip()
    record = get_confirmation(
        user_id=context["user_id"],
        session_id=context["session_id"],
        thread_id=context["thread_id"],
        confirmation_id=confirmation_id,
    )
    if record is None:
        return {"error": "CONFIRMATION_NOT_FOUND"}
    if record["tool"] not in {"cancel_order", "create_refund", "cancel_refund"}:
        return {"error": "CONFIRMATION_INVALID"}

    was_pending = record["status"] == "pending"
    service_context = {
        "user_id": context["user_id"],
        "session_id": context["session_id"],
        "thread_id": context["thread_id"],
        "confirmation_id": confirmation_id,
    }
    if decision == "reject":
        result = reject_confirmation(**service_context, now=current)
    else:
        approve = {
            "cancel_order": approve_cancel_confirmation,
            "create_refund": approve_refund_confirmation,
            "cancel_refund": approve_withdraw_confirmation,
        }[record["tool"]]
        result = approve(**service_context, now=current)

    emit_trace(
        "confirmation.decision",
        run_id=new_run_id(),
        source="explicit",
        session_id=context["session_id"],
        thread_id=context["thread_id"],
        tool=record["tool"],
        decision=decision,
        was_pending=was_pending,
        status=result.get("status"),
        error=result.get("error"),
    )

    reply = format_confirmation_result(
        tool=record["tool"],
        result=result,
    )

    history_saved = True
    warning = None
    if was_pending:
        context["messages"].append(
            {
                "role": "user",
                "content": "确认执行" if decision == "approve" else "拒绝操作",
            }
        )
        context["messages"].append({"role": "assistant", "content": reply})
        history_saved, warning = _save_context(context, current)

    active = load_active_confirmation(
        user_id=context["user_id"],
        session_id=context["session_id"],
        thread_id=context["thread_id"],
        now=current,
    )
    context["active"] = active

    return {
        "reply": reply,
        "session_id": context["session_id"],
        "thread_id": context["thread_id"],
        "pending_action": pending_action(active),
        "history_saved": history_saved,
        "warning": warning,
    }