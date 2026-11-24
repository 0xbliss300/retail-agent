"""用两笔新鲜的同价模拟订单，运行项目原有的聊天入口。"""

from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from app.storage import database
from scripts.demo_chat import main as chat


def seed_same_amount_orders() -> None:
    now = datetime.now(UTC)
    with closing(database.get_connection()) as conn, conn:
        conn.executemany(
            """
            INSERT INTO orders (order_id, user_id, status, paid_amount_cents, paid_at)
            VALUES (?, 'USER-001', 'paid', 8900, ?)
            """,
            [
                (f"ORD-89{i}", (now - timedelta(minutes=30 * i)).isoformat())
                for i in (1, 2)
            ],
        )


def main() -> None:
    with TemporaryDirectory(prefix="retail-cancel-by-amount-") as temp:
        previous_path = database.DB_PATH
        database.DB_PATH = Path(temp) / "retail.db"
        try:
            database.init_db()
            seed_same_amount_orders()
            chat([])
            with closing(database.get_connection()) as conn:
                for row in conn.execute("""
                    SELECT order_id, status FROM orders
                    WHERE order_id IN ('ORD-891', 'ORD-892') ORDER BY order_id
                """):
                    print(f"{row['order_id']} {row['status']}")
                count = conn.execute("""
                    SELECT COUNT(*) FROM refund_requests
                    WHERE order_id IN ('ORD-891', 'ORD-892') AND kind = 'cancellation'
                """).fetchone()[0]
                print(f"模拟取消退款流水 {count} 条")
        finally:
            database.DB_PATH = previous_path


if __name__ == "__main__":
    main()
