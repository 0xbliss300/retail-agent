import sqlite3
from pathlib import Path

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
                        'completed', 'failed', 'withdrawn'
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
                CHECK (tool IN ('create_refund', 'cancel_order', 'cancel_refund')),
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

def migrate_logistics(conn: sqlite3.Connection) -> None:
    """增量增加物流表；已有订单及业务记录原样保留。"""
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_order_user
        ON orders(order_id, user_id)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shipments (
            order_id TEXT PRIMARY KEY NOT NULL,
            user_id TEXT NOT NULL,
            carrier TEXT NOT NULL CHECK (length(trim(carrier)) > 0),
            tracking_number TEXT NOT NULL CHECK (length(trim(tracking_number)) > 0),
            UNIQUE (order_id, user_id),
            FOREIGN KEY (order_id, user_id) REFERENCES orders(order_id, user_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS logistics_events (
            event_id TEXT PRIMARY KEY NOT NULL,
            order_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            occurred_at TEXT NOT NULL CHECK (length(trim(occurred_at)) > 0),
            description TEXT NOT NULL CHECK (length(trim(description)) > 0),
            UNIQUE (order_id, occurred_at, description),
            FOREIGN KEY (order_id, user_id) REFERENCES shipments(order_id, user_id)
        )
    """)


def migrate_cancellation(conn: sqlite3.Connection) -> None:
    """增加取消业务约束和只追加日志；旧数据冲突时失败回滚，不删除历史。"""
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_refunds_cancellation_order
        ON refund_requests(order_id) WHERE kind = 'cancellation'
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_confirmations_id_user_order
        ON confirmations(confirmation_id, user_id, order_id)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS operation_logs (
            log_id TEXT PRIMARY KEY NOT NULL,
            user_id TEXT NOT NULL,
            order_id TEXT NOT NULL,
            confirmation_id TEXT NOT NULL UNIQUE,
            operation TEXT NOT NULL CHECK (operation = 'cancel_order'),
            previous_status TEXT NOT NULL CHECK (previous_status IN ('unpaid', 'paid')),
            new_status TEXT NOT NULL CHECK (new_status = 'canceled'),
            amount_cents INTEGER NOT NULL CHECK (typeof(amount_cents) = 'integer' AND amount_cents >= 0),
            reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
            params_fingerprint TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (order_id, operation),
            UNIQUE (user_id, idempotency_key),
            FOREIGN KEY (order_id, user_id) REFERENCES orders(order_id, user_id),
            FOREIGN KEY (confirmation_id, user_id, order_id)
                REFERENCES confirmations(confirmation_id, user_id, order_id)
        )
    """)
    for action in ("UPDATE", "DELETE"):
        conn.execute(f"""
            CREATE TRIGGER IF NOT EXISTS operation_logs_no_{action.lower()}
            BEFORE {action} ON operation_logs
            BEGIN SELECT RAISE(ABORT, 'operation_logs is append-only'); END
        """)


def migrate_refund_settlements(conn: sqlite3.Connection) -> None:
    """退款完成记录只追加保存；结算必须同时更新退款申请和订单。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS refund_settlements (
            settlement_id TEXT PRIMARY KEY NOT NULL,
            refund_id TEXT NOT NULL UNIQUE,
            order_id TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            amount_cents INTEGER NOT NULL
                CHECK (
                    typeof(amount_cents) = 'integer'
                    AND amount_cents >= 0
                ),
            previous_refund_status TEXT NOT NULL
                CHECK (previous_refund_status = 'pending'),
            new_refund_status TEXT NOT NULL
                CHECK (new_refund_status = 'completed'),
            previous_order_status TEXT NOT NULL
                CHECK (previous_order_status IN ('shipped', 'delivered')),
            new_order_status TEXT NOT NULL
                CHECK (new_order_status = 'refunded'),
            created_at TEXT NOT NULL,

            FOREIGN KEY (refund_id)
                REFERENCES refund_requests(refund_id),

            FOREIGN KEY (order_id, user_id)
                REFERENCES orders(order_id, user_id)
        )
    """)
    for action in ("UPDATE", "DELETE"):
        conn.execute(f"""
            CREATE TRIGGER IF NOT EXISTS refund_settlements_no_{action.lower()}
            BEFORE {action} ON refund_settlements
            BEGIN SELECT RAISE(ABORT, 'refund_settlements is append-only'); END
        """)


def _table_sql(conn: sqlite3.Connection, name: str) -> str | None:
    """读取建表语句，用于判断旧库的 CHECK 约束是否已包含新取值。"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row["sql"] if row is not None else None


def _needs_rebuild(conn: sqlite3.Connection, table: str, marker: str) -> bool:
    sql = _table_sql(conn, table)
    return sql is not None and marker not in sql


def migrate_withdraw_refund(conn: sqlite3.Connection) -> None:
    """为撤回退款申请扩展状态与工具取值；已有业务数据原样保留。

    SQLite 不能修改 CHECK 约束，只能建新表、搬数据、换名。
    调用方（init_db）必须在事务外关闭外键，并在提交前做完整性校验。
    """
    if _needs_rebuild(conn, "refund_requests", "'withdrawn'"):
        conn.execute("""
            CREATE TABLE refund_requests_withdraw (
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
                            'completed', 'failed', 'withdrawn'
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
            INSERT INTO refund_requests_withdraw (
                refund_id, order_id, user_id, kind, amount_cents, reason,
                status, idempotency_key, confirmation_id, created_at, updated_at
            )
            SELECT
                refund_id, order_id, user_id, kind, amount_cents, reason,
                status, idempotency_key, confirmation_id, created_at, updated_at
            FROM refund_requests
        """)
        conn.execute("DROP TABLE refund_requests")
        conn.execute(
            "ALTER TABLE refund_requests_withdraw RENAME TO refund_requests"
        )
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_refunds_active_after_sales
            ON refund_requests(order_id)
            WHERE kind = 'after_sales'
              AND status IN ('pending', 'approved')
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_refunds_cancellation_order
            ON refund_requests(order_id) WHERE kind = 'cancellation'
        """)

    if _needs_rebuild(conn, "confirmations", "'cancel_refund'"):
        conn.execute("""
            CREATE TABLE confirmations_withdraw (
                confirmation_id TEXT PRIMARY KEY NOT NULL,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                tool TEXT NOT NULL
                    CHECK (tool IN ('create_refund', 'cancel_order', 'cancel_refund')),
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
            INSERT INTO confirmations_withdraw (
                confirmation_id, user_id, session_id, thread_id, tool,
                order_id, params_json, params_fingerprint, summary, status,
                idempotency_key, created_at, expires_at,
                approved_at, consumed_at, result_json
            )
            SELECT
                confirmation_id, user_id, session_id, thread_id, tool,
                order_id, params_json, params_fingerprint, summary, status,
                idempotency_key, created_at, expires_at,
                approved_at, consumed_at, result_json
            FROM confirmations
        """)
        conn.execute("DROP TABLE confirmations")
        conn.execute(
            "ALTER TABLE confirmations_withdraw RENAME TO confirmations"
        )
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_confirmations_active_thread
            ON confirmations(user_id, session_id, thread_id)
            WHERE status IN ('pending', 'approved')
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_confirmations_id_user_order
            ON confirmations(confirmation_id, user_id, order_id)
        """)


def init_db():
    conn = get_connection()
    # 重建表期间必须关闭外键；关闭状态只在当前连接有效。
    conn.execute("PRAGMA foreign_keys = OFF")
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
        migrate_chat_threads(conn)
        migrate_logistics(conn)
        migrate_cancellation(conn)
        migrate_refund_settlements(conn)
        migrate_withdraw_refund(conn)

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

        # 外键在迁移期间关闭，提交前必须确认没有留下悬空引用。
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"迁移后外键校验失败：{len(violations)} 条")

        conn.commit()
        print(f"数据库初始化完成：{DB_PATH}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()

# 保存对话的表
def migrate_chat_threads(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_threads (
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            title TEXT NOT NULL,
            messages_json TEXT NOT NULL,
            version INTEGER NOT NULL CHECK (version >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, session_id, thread_id)
        )
    """)
if __name__ == "__main__":
    init_db()
