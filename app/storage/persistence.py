"""保存完整轮次的对话快照，不执行业务操作。"""

import json
from datetime import UTC, datetime

from app.storage.database import get_connection


class ConversationConflict(RuntimeError):
    """对话已被其他保存操作更新，或预期的对话不存在。"""


def list_conversations(*, user_id: str) -> list[dict]:
    """按最近更新时间列出本人对话摘要，不读取完整消息。"""
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("user_id 不能为空")
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT session_id, thread_id, title, created_at, updated_at
            FROM chat_threads
            WHERE user_id = ?
            ORDER BY updated_at DESC, rowid DESC
            """,
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def save_conversation(
    *,
    user_id: str,
    session_id: str,
    thread_id: str,
    messages: list,
    now: datetime,
    expected_version: int,
) -> int:
    """首次保存传0，后续保存传读取时的版本；返回新版本。"""
    context = {
        "user_id": user_id,
        "session_id": session_id,
        "thread_id": thread_id,
    }
    if not all(
        isinstance(value, str) and value.strip()
        for value in context.values()
    ):
        raise ValueError("用户、会话和线程编号不能为空")

    if type(expected_version) is not int or expected_version < 0:
        raise ValueError("expected_version 必须是非负整数")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须包含时区")

    if (
        not isinstance(messages, list)
        or not messages
        or not all(isinstance(message, dict) for message in messages)
        or messages[0].get("role") != "system"
        or messages[-1].get("role") != "assistant"
        or messages[-1].get("tool_calls")
    ):
        raise ValueError("只能保存以助手最终回复结束的完整轮次")

    first_question = next(
        (
            message.get("content")
            for message in messages
            if message.get("role") == "user"
        ),
        None,
    )
    if not isinstance(first_question, str) or not first_question.strip():
        raise ValueError("对话必须包含用户问题")

    timestamp = now.astimezone(UTC).isoformat()
    new_version = expected_version + 1
    record = {
        **context,
        "title": " ".join(first_question.split())[:60],
        "messages_json": json.dumps(messages, ensure_ascii=False),
        "version": new_version,
        "expected_version": expected_version,
        "timestamp": timestamp,
    }

    conn = get_connection()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            if expected_version == 0:
                cursor = conn.execute(
                    """
                    INSERT INTO chat_threads (
                        user_id, session_id, thread_id,
                        title, messages_json, version,
                        created_at, updated_at
                    )
                    VALUES (
                        :user_id, :session_id, :thread_id,
                        :title, :messages_json, :version,
                        :timestamp, :timestamp
                    )
                    ON CONFLICT(user_id, session_id, thread_id)
                    DO NOTHING
                    """,
                    record,
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE chat_threads
                    SET title = :title,
                        messages_json = :messages_json,
                        version = :version,
                        updated_at = :timestamp
                    WHERE user_id = :user_id
                      AND session_id = :session_id
                      AND thread_id = :thread_id
                      AND version = :expected_version
                    """,
                    record,
                )

            if cursor.rowcount != 1:
                raise ConversationConflict(
                    "对话版本已变化或记录不存在，请重新读取后再保存。"
                )

        return new_version
    finally:
        conn.close()


def load_conversation(
    *,
    user_id: str,
    session_id: str,
    thread_id: str,
) -> dict | None:
    """按完整身份上下文读取快照；无匹配记录返回None。"""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT title, messages_json, version, created_at, updated_at
            FROM chat_threads
            WHERE user_id = ?
              AND session_id = ?
              AND thread_id = ?
            """,
            (user_id, session_id, thread_id),
        ).fetchone()
        if row is None:
            return None

        result = dict(row)
        result["messages"] = json.loads(result.pop("messages_json"))
        return result
    finally:
        conn.close()
