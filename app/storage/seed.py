"""加载本地开发使用的模拟订单、退款和物流，不代表真实业务数据。"""

from datetime import UTC, datetime, timedelta

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


def seed_cancellation_orders(*, now: datetime | None = None) -> None:
    """新增取消演示订单；已有订单状态和付款时间不重置。"""
    reference = now if now is not None else datetime.now(UTC)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("now 必须包含时区")
    paid_at = (reference.astimezone(UTC) - timedelta(hours=1)).isoformat()
    conn = get_connection()
    try:
        with conn:
            conn.executemany(
                """
                INSERT INTO orders (order_id, user_id, status, paid_amount_cents, paid_at)
                VALUES (?, 'USER-001', ?, ?, ?)
                ON CONFLICT(order_id) DO NOTHING
                """,
                [("ORD-3001", "unpaid", 0, None), ("ORD-3002", "paid", 19900, paid_at)],
            )
    finally:
        conn.close()


def seed_logistics() -> None:
    """为模拟订单补充物流；已有物流与人工修改的轨迹不覆盖、不追加。"""
    conn = get_connection()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            # 同名订单若已属于其他用户，不为它附加当前用户的模拟物流。
            order = conn.execute(
                "SELECT 1 FROM orders WHERE order_id = ? AND user_id = ?",
                ("ORD-2002", "USER-001"),
            ).fetchone()
            if order is None:
                return
            inserted = conn.execute(
                """
                INSERT INTO shipments (order_id, user_id, carrier, tracking_number)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(order_id) DO NOTHING
                """,
                ("ORD-2002", "USER-001", "模拟速递", "MOCK2002001"),
            )
            if inserted.rowcount == 0:
                return
            conn.executemany(
                """
                INSERT INTO logistics_events (event_id, order_id, user_id, occurred_at, description)
                VALUES (?, 'ORD-2002', 'USER-001', ?, ?)
                """,
                [
                    ("mock_2002_pickup", "2026-10-26T03:00:00+00:00", "模拟：快递已揽收"),
                    ("mock_2002_transit", "2026-10-27T04:00:00+00:00", "模拟：到达分拨中心"),
                    ("mock_2002_dispatch", "2026-10-28T02:00:00+00:00", "模拟：快递正在派送"),
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
