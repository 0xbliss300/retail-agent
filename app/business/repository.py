"""业务数据访问；写操作使用调用方提供的事务连接。"""
import sqlite3
from app.storage.database import get_connection


def get_order_for_user(*, user_id: str, order_id: str) -> dict | None:
    """按可信用户身份读取订单，不区分不存在和无权访问。"""
    conn = get_connection()

    try:
        row = conn.execute(
            """
            SELECT order_id, user_id, status, paid_amount_cents,
                   paid_at, shipped_at, delivered_at
            FROM orders
            WHERE order_id = ? AND user_id = ?
            """,
            (order_id, user_id),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def get_active_refund(
    *,
    user_id: str,
    order_id: str,
) -> dict | None:
    """读取当前用户在该订单上处理中的售后退款申请。"""
    conn = get_connection()

    try:
        row = conn.execute(
            """
            SELECT refund_id, order_id, user_id, kind,
                   amount_cents, reason, status,
                   created_at, updated_at
            FROM refund_requests
            WHERE user_id = ?
              AND order_id = ?
              AND kind = 'after_sales'
              AND status IN ('pending', 'approved')
            """,
            (user_id, order_id),
        ).fetchone()

        return dict(row) if row is not None else None
    finally:
        conn.close()

def insert_pending_confirmation(
    conn: sqlite3.Connection,
    *,
    record: dict,
) -> bool:
    """调用方必须先开启写事务；本函数不提交。"""
    active = conn.execute(
        """
        SELECT confirmation_id
        FROM confirmations
        WHERE user_id = ?
          AND session_id = ?
          AND thread_id = ?
          AND status IN ('pending', 'approved')
        """,
        (
            record["user_id"],
            record["session_id"],
            record["thread_id"],
        ),
    ).fetchone()

    if active is not None:
        return False

    conn.execute(
        """
        INSERT INTO confirmations (
            confirmation_id, user_id, session_id, thread_id,
            tool, order_id, params_json, params_fingerprint,
            summary, status, idempotency_key,
            created_at, expires_at
        )
        VALUES (
            :confirmation_id, :user_id, :session_id, :thread_id,
            :tool, :order_id, :params_json, :params_fingerprint,
            :summary, :status, :idempotency_key,
            :created_at, :expires_at
        )
        """,
        record,
    )
    return True


def get_confirmation_for_context(
    conn: sqlite3.Connection,
    *,
    confirmation_id: str,
    user_id: str,
    session_id: str,
    thread_id: str,
) -> dict | None:
    """只读取属于当前用户、会话和线程的确认记录。"""
    row = conn.execute(
        """
        SELECT *
        FROM confirmations
        WHERE confirmation_id = ?
          AND user_id = ?
          AND session_id = ?
          AND thread_id = ?
        """,
        (confirmation_id, user_id, session_id, thread_id),
    ).fetchone()

    return dict(row) if row is not None else None


def get_active_confirmation_for_context(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    session_id: str,
    thread_id: str,
) -> dict | None:
    """部分唯一索引保证同一上下文最多一条活跃操作。"""
    row = conn.execute(
        """
        SELECT * FROM confirmations
        WHERE user_id = ? AND session_id = ? AND thread_id = ?
          AND status IN ('pending', 'approved')
        """,
        (user_id, session_id, thread_id),
    ).fetchone()
    return dict(row) if row is not None else None


def close_pending_confirmation(
    conn: sqlite3.Connection,
    *,
    confirmation_id: str,
    user_id: str,
    session_id: str,
    thread_id: str,
    status: str,
) -> None:
    """调用方持有写事务；只允许 pending 转为拒绝或过期。"""
    if status not in {"rejected", "expired"}:
        raise ValueError("只允许关闭为 rejected 或 expired")

    cursor = conn.execute(
        """
        UPDATE confirmations
        SET status = ?
        WHERE confirmation_id = ?
          AND user_id = ?
          AND session_id = ?
          AND thread_id = ?
          AND status = 'pending'
        """,
        (status, confirmation_id, user_id, session_id, thread_id),
    )

    if cursor.rowcount != 1:
        raise RuntimeError("待确认操作状态发生变化")
