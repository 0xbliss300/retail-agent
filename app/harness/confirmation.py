"""准备、拒绝待确认操作并处理过期；不批准确认、不创建退款申请。"""

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.business.repository import (
    close_pending_confirmation,
    get_active_confirmation_for_context,
    get_confirmation_for_context,
    insert_pending_confirmation,
    get_pending_refund_confirmations_for_user,
)
from app.business.service import check_refund_eligibility
from app.storage.database import get_connection


CONFIRMATION_TTL = timedelta(minutes=10)


def _expire_if_due(conn: sqlite3.Connection, record: dict | None, now: datetime) -> bool:
    """只关闭到期的 pending；approved 留给后续执行恢复流程。"""
    if record is None or record["status"] != "pending":
        return False
    expires_at = datetime.fromisoformat(record["expires_at"])
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise ValueError("确认记录的过期时间必须包含时区")
    if now < expires_at:
        return False
    close_pending_confirmation(
        conn,
        confirmation_id=record["confirmation_id"],
        user_id=record["user_id"],
        session_id=record["session_id"],
        thread_id=record["thread_id"],
        status="expired",
    )
    return True

def list_resumable_confirmations(
    *,
    user_id: str,
    now: datetime,
) -> list[dict]:
    """列出本人未过期的待确认退款操作。"""
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("user_id 不能为空")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须包含时区")

    conn = get_connection()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            records = get_pending_refund_confirmations_for_user(
                conn,
                user_id=user_id,
            )
            return [
                record
                for record in records
                if not _expire_if_due(conn, record, now)
            ]
    finally:
        conn.close()

def load_active_confirmation(
    *, user_id: str, session_id: str, thread_id: str, now: datetime,
) -> dict | None:
    """读取当前会话操作，顺便释放已到期的 pending 记录。"""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须包含时区")
    conn = get_connection()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            record = get_active_confirmation_for_context(
                conn, user_id=user_id, session_id=session_id, thread_id=thread_id,
            )
            if _expire_if_due(conn, record, now):
                return None
            return record
    finally:
        conn.close()


def prepare_refund_confirmation(
    *,
    user_id: str,
    session_id: str,
    thread_id: str,
    order_id: str,
    reason: str,
    now: datetime,
) -> dict:

    values = (user_id, session_id, thread_id, order_id, reason)
    if not all(isinstance(v, str) and v.strip() for v in values):
        return {"ok": False, "error": "INVALID_ARGUMENTS"}

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须包含时区")

    # 获取现在的时间
    now = now.astimezone(timezone.utc)
    order_id = order_id.strip()
    reason = reason.strip()

    # 获取是否有退款资格
    eligibility = check_refund_eligibility(
        user_id=user_id,
        order_id=order_id,
        now=now,
    )

    # 判断是否符合退款资格
    if not eligibility["ok"] or not eligibility["eligible"]:
        return eligibility

    # 获取退款的数目
    amount = eligibility["amount_cents"]
    # json化参数
    params = {
        "order_id": order_id,
        "reason": reason,
        "amount_cents": amount,
    }
    params_json = json.dumps(
        params,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    # 生成指纹
    fingerprint = hashlib.sha256(
        params_json.encode("utf-8")
    ).hexdigest()

    # 构造确认单
    confirmation_id = f"cfm_{uuid4().hex}"
    expires_at = (now + CONFIRMATION_TTL).isoformat() # 设置的10min失效
    summary = (
        f"为订单 {order_id} 提交全额退款申请，"
        f"金额 {amount // 100}.{amount % 100:02d} 元，"
        f"原因：{reason}。提交申请不代表退款已到账。"
    )

    record = {
        "confirmation_id": confirmation_id,
        "user_id": user_id,
        "session_id": session_id,
        "thread_id": thread_id,
        "tool": "create_refund",
        "order_id": order_id,
        "params_json": params_json,
        "params_fingerprint": fingerprint,
        "summary": summary,
        "status": "pending",
        "idempotency_key": f"confirm:{confirmation_id}",
        "created_at": now.isoformat(),
        "expires_at": expires_at,
    }

    # 写入数据库
    conn = get_connection()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            active = get_active_confirmation_for_context(
                conn, user_id=user_id, session_id=session_id, thread_id=thread_id,
            )
            _expire_if_due(conn, active, now)
            inserted = insert_pending_confirmation(
                conn,
                record=record,
            )
    finally:
        conn.close()

    # 判断数据库操作是否成功
    if not inserted:
        return {"ok": False, "error": "PENDING_ACTION_EXISTS"}

    return {
        "ok": True,
        "status": "awaiting_confirmation",
        "confirmation_id": confirmation_id,
        "summary": summary,
        "expires_at": expires_at,
    }

# 拒绝待确认操作
def reject_confirmation(
    *,
    user_id: str,
    session_id: str,
    thread_id: str,
    confirmation_id: str,
    now: datetime,
) -> dict:
    """拒绝当前上下文中的 pending 操作；到期时保存 expired。"""
    values = (user_id, session_id, thread_id, confirmation_id)
    if not all(isinstance(v, str) and v.strip() for v in values):
        return {"ok": False, "error": "INVALID_ARGUMENTS"}

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须包含时区")

    now = now.astimezone(timezone.utc)
    context = {
        "confirmation_id": confirmation_id,
        "user_id": user_id,
        "session_id": session_id,
        "thread_id": thread_id,
    }

    conn = get_connection()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            record = get_confirmation_for_context(conn, **context)

            if record is None:
                return {"ok": False, "error": "CONFIRMATION_NOT_FOUND"}

            if record["status"] == "rejected":
                return {"ok": True, "status": "rejected"}

            if record["status"] == "expired":
                return {"ok": False, "error": "CONFIRMATION_EXPIRED"}

            if record["status"] != "pending":
                return {"ok": False, "error": "CONFIRMATION_NOT_PENDING"}

            expires_at = datetime.fromisoformat(record["expires_at"])
            if expires_at.tzinfo is None or expires_at.utcoffset() is None:
                raise ValueError("确认记录的过期时间必须包含时区")

            status = "expired" if now >= expires_at else "rejected"
            close_pending_confirmation(conn, status=status, **context)

        # 离开事务、提交状态后再返回；expired 是已保存的业务结果。
        if status == "expired":
            return {"ok": False, "error": "CONFIRMATION_EXPIRED"}

        return {"ok": True, "status": "rejected"}
    finally:
        conn.close()
