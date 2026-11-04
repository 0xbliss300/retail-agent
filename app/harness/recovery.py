"""工具执行的重试决策，以及断线后查询上一次操作的真实结果。"""

import json
from datetime import UTC, datetime

from app.business.repository import get_latest_confirmation_for_context
from app.storage.database import get_connection

READ_ONLY_TOOLS = frozenset({
    "list_orders",
    "list_refunds",
    "get_logistics",
    "get_order",
    "get_policy",
    "check_refund_eligibility",
    "get_refund_status",
})

WRITE_TOOLS = frozenset({
    "cancel_order",
    "prepare_refund_confirmation",
    "withdraw_refund",
})

# 只读工具遇到超时或数据库忙时可以安全重试；业务拒绝和普通异常不重试。
RETRYABLE_ERRORS = frozenset({"TOOL_TIMEOUT", "TOOL_BUSY"})
READ_TOOL_MAX_RETRIES = 1


def attempts_for(tool_name: str) -> int:
    """只读工具最多执行一次加一次重试；写工具只执行一次。"""
    return 1 + READ_TOOL_MAX_RETRIES if tool_name in READ_ONLY_TOOLS else 1


def should_retry(tool_name: str, result: dict) -> bool:
    """仅对只读工具的超时或数据库忙结果重试。"""
    return (
        tool_name in READ_ONLY_TOOLS
        and isinstance(result, dict)
        and result.get("error") in RETRYABLE_ERRORS
    )


def describe_recovery(
    *,
    user_id: str,
    session_id: str,
    thread_id: str,
    now: datetime,
) -> dict:
    """只读查询：该会话最近一次高风险操作现在处于什么状态。

    不改数据库。已过期的 pending 记录在下一次正常对话中才落地为 expired，
    这里只在返回结果时按过期判断，避免查询接口产生写副作用。
    """
    values = (user_id, session_id, thread_id)
    if not all(isinstance(value, str) and value.strip() for value in values):
        return {"ok": False, "error": "INVALID_ARGUMENTS"}
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须包含时区")
    now = now.astimezone(UTC)

    conn = get_connection()
    try:
        record = get_latest_confirmation_for_context(
            conn,
            user_id=user_id,
            session_id=session_id,
            thread_id=thread_id,
        )
    finally:
        conn.close()

    if record is None:
        return {
            "ok": True,
            "state": "idle",
            "last_state": None,
            "tool": None,
            "order_id": None,
            "summary": None,
            "expires_at": None,
            "result": None,
        }

    status = record["status"]
    payload = {
        "ok": True,
        "tool": record["tool"],
        "order_id": record["order_id"],
        "summary": record["summary"],
        "expires_at": record["expires_at"],
        "last_state": status,
        "result": None,
    }

    if status == "consumed":
        saved = None
        if record["result_json"]:
            try:
                saved = json.loads(record["result_json"])
            except ValueError:
                saved = None
        return {**payload, "state": "executed", "result": saved}

    if status in {"pending", "approved"}:
        expires_at = datetime.fromisoformat(record["expires_at"])
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("确认记录的过期时间必须包含时区")
        if now >= expires_at:
            # 与 pending 区分：过期的操作不会再执行。
            return {**payload, "state": "idle", "last_state": "expired"}
        return {**payload, "state": "awaiting_confirmation"}

    # rejected、expired：已结束且没有执行业务写入。
    return {**payload, "state": "idle"}