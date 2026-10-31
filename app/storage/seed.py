"""加载本地开发使用的模拟订单和退款，不代表真实业务数据。"""

from app.storage.database import get_connection


def seed_orders() -> None:
    conn = get_connection()

    try:
        with conn:
            conn.executemany(
                """
                INSERT INTO orders (
                    order_id,
                    user_id,
                    status,
                    paid_amount_cents,
                    paid_at,
                    shipped_at,
                    delivered_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO NOTHING
                """,
                [
                    (
                        "ORD-2001",
                        "USER-001",
                        "shipped",
                        12900,
                        "2026-10-25T02:00:00+00:00",
                        "2026-10-26T02:00:00+00:00",
                        None,
                    ),
                    (
                        "ORD-2002",
                        "USER-001",
                        "shipped",
                        8900,
                        "2026-10-25T02:00:00+00:00",
                        "2026-10-26T02:00:00+00:00",
                        None,
                    ),
                    (
                        "ORD-2003",
                        "USER-001",
                        "shipped",
                        6900,
                        "2026-10-29T02:00:00+00:00",
                        "2026-10-30T02:00:00+00:00",
                        None,
                    ),
                ],
            )
    finally:
        conn.close()


def seed_refunds() -> None:
    """仅供本地开发加载模拟记录，不是正式退款申请入口。"""
    conn = get_connection()

    try:
        with conn:
            order = conn.execute(
                """
                SELECT paid_amount_cents
                FROM orders
                WHERE order_id = ? AND user_id = ?
                """,
                ("ORD-2001", "USER-001"),
            ).fetchone()

            if order is None:
                raise RuntimeError("请先加载 ORD-2001 模拟订单")

            conn.execute(
                """
                INSERT INTO refund_requests (
                    refund_id, order_id, user_id, kind,
                    amount_cents, reason, status,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(refund_id) DO NOTHING
                """,
                (
                    "RF-DEMO-001",
                    "ORD-2001",
                    "USER-001",
                    "after_sales",
                    order["paid_amount_cents"],
                    "模拟场景：商品破损",
                    "pending",
                    "2026-10-27T02:00:00+00:00",
                    "2026-10-27T02:00:00+00:00",
                ),
            )
    finally:
        conn.close()
