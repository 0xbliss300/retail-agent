from pathlib import Path
import sqlite3

# 获取当前项目的绝对地址
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "retail.db"

def get_connection():
    # 防止父目录不存在
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)

    conn.execute("PRAGMA foreign_keys = ON")

    # 让查询结果可以通过列名访问
    conn.row_factory = sqlite3.Row

    return conn

# 创建退款申请表
def migrate_refund_requests(conn: sqlite3.Connection) -> None:
    # 为下面的组合外键提供唯一的引用目标。
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_order_user
        ON orders(order_id, user_id)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS refund_requests (
            refund_id TEXT PRIMARY KEY NOT NULL,
            order_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL
                CHECK (kind IN ('after_sales', 'cancellation')),
            amount_cents INTEGER NOT NULL
                CHECK (
                    typeof(amount_cents) = 'integer'
                    AND amount_cents >= 0
                ),
            reason TEXT NOT NULL
                CHECK (length(trim(reason)) > 0),
            status TEXT NOT NULL
                CHECK (
                    status IN (
                        'pending', 'approved', 'rejected',
                        'completed', 'failed'
                    )
                ),
            idempotency_key TEXT,
            confirmation_id TEXT UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,

            FOREIGN KEY (order_id, user_id)
                REFERENCES orders(order_id, user_id),

            UNIQUE (user_id, idempotency_key)
        )
    """)

    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_refunds_active_after_sales
        ON refund_requests(order_id)
        WHERE kind = 'after_sales'
          AND status IN ('pending', 'approved')
    """)

# 创建待确认操作表
def migrate_confirmations(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS confirmations (
            confirmation_id TEXT PRIMARY KEY NOT NULL,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            tool TEXT NOT NULL
                CHECK (tool IN ('create_refund', 'cancel_order')),
            order_id TEXT NOT NULL,
            params_json TEXT NOT NULL,
            params_fingerprint TEXT NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (
                    status IN (
                        'pending', 'approved', 'rejected',
                        'expired', 'consumed'
                    )
                ),
            idempotency_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            approved_at TEXT,
            consumed_at TEXT,
            result_json TEXT,

            FOREIGN KEY (order_id, user_id)
                REFERENCES orders(order_id, user_id),

            UNIQUE (user_id, idempotency_key)
        )
    """)

    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_confirmations_active_thread
        ON confirmations(user_id, session_id, thread_id)
        WHERE status IN ('pending', 'approved')
    """)

def init_db():
    conn = get_connection()
    conn.execute("BEGIN")

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                status TEXT NOT NULL,
                paid_amount_cents INTEGER NOT NULL
            )
        """)

        # 约定：时间使用 UTC ISO 8601 文本，例如 2026-10-27T02:00:00+00:00。
        # 已有订单没有可靠的历史时间，新增字段先保留 NULL。
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(orders)")
        }

        for column in ("paid_at", "shipped_at", "delivered_at"):
            if column not in columns:
                conn.execute(
                    f"ALTER TABLE orders ADD COLUMN {column} TEXT"
                )

        migrate_refund_requests(conn)
        migrate_confirmations(conn)

        conn.executemany(
            """
            INSERT INTO orders (
                order_id, user_id, status, paid_amount_cents
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(order_id) DO NOTHING
            """,
            [
                ("ORD-1001", "USER-001", "paid", 9900),
                ("ORD-1002", "USER-002", "shipped", 15900),
            ],
        )

        conn.commit()
        print(f"数据库初始化完成：{DB_PATH}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()