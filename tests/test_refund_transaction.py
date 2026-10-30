from datetime import datetime

from app.business.service import check_refund_eligibility
from app.storage import database


def test_eligibility_uses_callers_transaction(tmp_path, monkeypatch):
    # 使用临时数据库，不改你的开发数据库。
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()

    now = datetime.fromisoformat("2026-10-30T02:00:00+00:00")
    conn = database.get_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")

        # 这条订单尚未提交，资格检查必须使用当前连接才能看到。
        conn.execute(
            """
            INSERT INTO orders (
                order_id, user_id, status,
                paid_amount_cents, shipped_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "ORD-TX",
                "USER-TX",
                "shipped",
                8900,
                "2026-10-29T02:00:00+00:00",
            ),
        )

        result = check_refund_eligibility(
            user_id="USER-TX",
            order_id="ORD-TX",
            now=now,
            conn=conn,
        )
        assert result["eligible"] is True
        assert result["amount_cents"] == 8900

        # 同一事务内新增处理中申请，复查应立即看到它。
        conn.execute(
            """
            INSERT INTO refund_requests (
                refund_id, order_id, user_id, kind,
                amount_cents, reason, status,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "RF-TX", "ORD-TX", "USER-TX", "after_sales",
                8900, "商品破损", "pending",
                now.isoformat(), now.isoformat(),
            ),
        )

        result = check_refund_eligibility(
            user_id="USER-TX",
            order_id="ORD-TX",
            now=now,
            conn=conn,
        )
        assert result["eligible"] is False
        assert result["policy_code"] == "ACTIVE_REFUND_EXISTS"

        # 查询函数不能擅自提交或关闭调用方的连接。
        assert conn.in_transaction
        conn.rollback()

        # 回滚后，两条记录都不应留下。
        assert conn.execute(
            "SELECT COUNT(*) FROM orders WHERE order_id = 'ORD-TX'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM refund_requests WHERE refund_id = 'RF-TX'"
        ).fetchone()[0] == 0
    finally:
        conn.close()