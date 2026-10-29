"""业务数据读取，不调用模型、不判断退款时间规则。"""

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
