"""业务数据访问；写操作使用调用方提供的事务连接。"""
import sqlite3

from app.storage.database import get_connection


def get_order_for_user(
    *, 
    user_id: str, 
    order_id: str,
    conn: sqlite3.Connection | None = None,
) -> dict | None:
    """按可信用户身份读取订单，不区分不存在和无权访问。"""

    owns_connection = conn is None

    if conn is None:
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
        if owns_connection:
            conn.close()


def get_orders_for_user(*, user_id: str, limit: int, offset: int) -> list[dict]:
    """先按可信用户过滤，再按订单号稳定排序分页；不返回内部身份字段。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT order_id, status, paid_amount_cents, paid_at, shipped_at, delivered_at
            FROM orders
            WHERE user_id = ?
            ORDER BY order_id ASC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_logistics_for_user(*, user_id: str, order_id: str) -> dict | None:
    """在同一读取快照中查询本人物流与轨迹，不返回身份或内部事件编号。"""
    conn = get_connection()
    try:
        with conn:
            conn.execute("BEGIN")
            row = conn.execute(
                """
                SELECT s.carrier, s.tracking_number
                FROM shipments AS s
                JOIN orders AS o ON o.order_id = s.order_id AND o.user_id = s.user_id
                WHERE s.order_id = ? AND s.user_id = ?
                """,
                (order_id, user_id),
            ).fetchone()
            if row is None:
                return None
            events = conn.execute(
                """
                SELECT occurred_at, description
                FROM logistics_events
                WHERE order_id = ? AND user_id = ?
                ORDER BY occurred_at ASC, event_id ASC
                """,
                (order_id, user_id),
            ).fetchall()
            return {**dict(row), "events": [dict(event) for event in events]}
    finally:
        conn.close()


def list_refunds_for_user(
    *,
    user_id: str,
    status: str | None,
    limit: int,
    offset: int,
) -> list[dict]:
    """按可信用户列出售后申请；只读，不含取消产生的退款流水。"""
    sql = """
        SELECT r.refund_id, r.order_id, r.amount_cents, r.status, r.reason,
               r.created_at, r.updated_at, o.status AS order_status
        FROM refund_requests AS r
        JOIN orders AS o
          ON o.order_id = r.order_id
         AND o.user_id = r.user_id
        WHERE r.user_id = ?
          AND r.kind = 'after_sales'
    """
    params: list = [user_id]
    if status is not None:
        sql += " AND r.status = ?"
        params.append(status)
    sql += " ORDER BY r.created_at DESC, r.rowid DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_refund_for_user(*, user_id: str, refund_id: str) -> dict | None:
    """按申请编号读取本人的售后申请；跨用户按不存在处理。"""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT r.refund_id, r.order_id, r.amount_cents, r.reason, r.status,
                   r.created_at, r.updated_at, o.status AS order_status,
                   o.paid_amount_cents, o.paid_at, o.shipped_at, o.delivered_at
            FROM refund_requests AS r
            JOIN orders AS o
              ON o.order_id = r.order_id
             AND o.user_id = r.user_id
            WHERE r.refund_id = ?
              AND r.user_id = ?
              AND r.kind = 'after_sales'
            """,
            (refund_id, user_id),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def get_active_refund(
    *,
    user_id: str,
    order_id: str,
    conn: sqlite3.Connection | None = None
) -> dict | None:
    """读取当前用户在该订单上处理中的售后退款申请。"""
    owns_connection = conn is None
    if conn is None:
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
        if owns_connection:
            conn.close()


def list_active_refund_order_ids(*, user_id: str) -> set[str]:
    """一次读出本人所有处理中的售后退款订单号，供批量资格判断使用。

    只读，不参与写事务；写事务内的单笔复查仍用 get_active_refund。
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT order_id
            FROM refund_requests
            WHERE user_id = ?
              AND kind = 'after_sales'
              AND status IN ('pending', 'approved')
            """,
            (user_id,),
        ).fetchall()
        return {row["order_id"] for row in rows}
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


def get_latest_confirmation_for_context(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    session_id: str,
    thread_id: str,
) -> dict | None:
    """读取该会话最近一条确认记录，不限状态；用于断线后查询结果。"""
    row = conn.execute(
        """
        SELECT *
        FROM confirmations
        WHERE user_id = ?
          AND session_id = ?
          AND thread_id = ?
        ORDER BY created_at DESC, rowid DESC
        LIMIT 1
        """,
        (user_id, session_id, thread_id),
    ).fetchone()
    return dict(row) if row is not None else None


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


def get_active_confirmation_for_context(    conn: sqlite3.Connection,
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

def insert_refund_request(
    conn: sqlite3.Connection,
    *,
    record: dict,
) -> None:
    """在调用方事务中插入退款申请，不自行提交。"""
    conn.execute(
        """
        INSERT INTO refund_requests (
            refund_id, order_id, user_id, kind,
            amount_cents, reason, status,
            idempotency_key, confirmation_id,
            created_at, updated_at
        )
        VALUES (
            :refund_id, :order_id, :user_id, 'after_sales',
            :amount_cents, :reason, 'pending',
            :idempotency_key, :confirmation_id,
            :created_at, :updated_at
        )
        """,
        record,
    )


def consume_refund_confirmation(
    conn: sqlite3.Connection,
    *,
    context: dict,
    result_json: str,
    timestamp: str,
) -> None:
    """记录批准和执行结果；只允许消费当前上下文的 pending。"""
    cursor = conn.execute(
        """
        UPDATE confirmations
        SET status = 'consumed',
            approved_at = :timestamp,
            consumed_at = :timestamp,
            result_json = :result_json
        WHERE confirmation_id = :confirmation_id
          AND user_id = :user_id
          AND session_id = :session_id
          AND thread_id = :thread_id
          AND tool = 'create_refund'
          AND status = 'pending'
        """,
        {
            **context,
            "timestamp": timestamp,
            "result_json": result_json,
        },
    )
    if cursor.rowcount != 1:
        raise RuntimeError("待确认操作状态发生变化")


def get_latest_after_sales_refund(
    *,
    user_id: str,
    order_id: str,
) -> dict | None:
    """查询当前用户在该订单上的最新售后申请，包含已结束状态。"""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT refund_id, amount_cents, reason, status,
                   created_at, updated_at
            FROM refund_requests
            WHERE user_id = ?
              AND order_id = ?
              AND kind = 'after_sales'
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (user_id, order_id),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()

def get_refund_for_settlement(
    conn: sqlite3.Connection,
    *,
    refund_id: str,
) -> dict | None:
    """读取待结算退款及其订单；调用方持有事务。"""
    row = conn.execute(
        """
        SELECT r.refund_id, r.order_id, r.user_id, r.kind,
               r.amount_cents, r.reason, r.status,
               r.created_at, r.updated_at,
               o.status AS order_status,
               o.paid_amount_cents
        FROM refund_requests AS r
        JOIN orders AS o
          ON o.order_id = r.order_id
         AND o.user_id = r.user_id
        WHERE r.refund_id = ?
        """,
        (refund_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def get_refund_settlement(
    conn: sqlite3.Connection,
    *,
    refund_id: str,
) -> dict | None:
    """读取已保存的结算结果，用于重复调用时重放。"""
    row = conn.execute(
        "SELECT * FROM refund_settlements WHERE refund_id = ?",
        (refund_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def complete_refund_in_transaction(
    conn: sqlite3.Connection,
    *,
    refund_id: str,
    timestamp: str,
) -> None:
    """只允许 pending 变为 completed；调用方负责提交或回滚。"""
    cursor = conn.execute(
        """
        UPDATE refund_requests
        SET status = 'completed', updated_at = ?
        WHERE refund_id = ? AND status = 'pending'
        """,
        (timestamp, refund_id),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("结算时退款申请状态发生变化")


def refund_order_in_transaction(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    order_id: str,
    expected_status: str,
) -> None:
    """把已发货或已送达订单置为 refunded；状态变化时由调用方回滚。"""
    cursor = conn.execute(
        """
        UPDATE orders
        SET status = 'refunded'
        WHERE order_id = ? AND user_id = ? AND status = ?
        """,
        (order_id, user_id, expected_status),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("结算时订单状态发生变化")


def insert_refund_settlement(
    conn: sqlite3.Connection,
    *,
    record: dict,
) -> None:
    """在调用方事务中写入只追加结算记录，不自行提交。"""
    conn.execute(
        """
        INSERT INTO refund_settlements (
            settlement_id, refund_id, order_id, user_id, amount_cents,
            previous_refund_status, new_refund_status,
            previous_order_status, new_order_status, created_at
        )
        VALUES (
            :settlement_id, :refund_id, :order_id, :user_id, :amount_cents,
            :previous_refund_status, :new_refund_status,
            :previous_order_status, :new_order_status, :created_at
        )
        """,
        record,
    )


def get_pending_confirmations_for_user(
    conn: sqlite3.Connection,
    *,
    user_id: str,
) -> list[dict]:
    """读取本人待确认的退款或取消操作，由调用方处理过期。"""
    rows = conn.execute(
        """
        SELECT *
        FROM confirmations
        WHERE user_id = ?
          AND tool IN ('create_refund', 'cancel_order', 'cancel_refund')
          AND status = 'pending'
        ORDER BY created_at DESC, rowid DESC
        """,
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_cancel_log_for_order(conn: sqlite3.Connection, *, user_id: str, order_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM operation_logs WHERE user_id=? AND order_id=? AND operation='cancel_order'",
        (user_id, order_id),
    ).fetchone()
    return dict(row) if row is not None else None


def get_blocking_refund_for_cancel(conn: sqlite3.Connection, *, user_id: str, order_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT refund_id FROM refund_requests
        WHERE user_id=? AND order_id=?
          AND (kind='cancellation' OR status IN ('pending','approved','completed'))
        LIMIT 1
        """,
        (user_id, order_id),
    ).fetchone()
    return dict(row) if row is not None else None


def cancel_order_in_transaction(
    conn: sqlite3.Connection, *, user_id: str, order_id: str, expected_status: str,
) -> None:
    cursor = conn.execute(
        "UPDATE orders SET status='canceled' WHERE user_id=? AND order_id=? AND status=?",
        (user_id, order_id, expected_status),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("取消时订单状态发生变化")


def insert_cancellation_refund(conn: sqlite3.Connection, *, record: dict) -> None:
    """与订单、日志和确认结果使用同一事务；不调用真实支付系统。"""
    conn.execute(
        """
        INSERT INTO refund_requests (
            refund_id, order_id, user_id, kind, amount_cents, reason, status,
            idempotency_key, confirmation_id, created_at, updated_at
        ) VALUES (
            :refund_id, :order_id, :user_id, 'cancellation', :amount_cents, :reason, 'completed',
            :idempotency_key, :confirmation_id, :timestamp, :timestamp
        )
        """, record,
    )


def insert_cancel_operation_log(conn: sqlite3.Connection, *, record: dict) -> None:
    conn.execute(
        """
        INSERT INTO operation_logs (
            log_id,user_id,order_id,confirmation_id,operation,previous_status,new_status,
            amount_cents,reason,params_fingerprint,idempotency_key,created_at
        ) VALUES (
            :log_id,:user_id,:order_id,:confirmation_id,'cancel_order',:previous_status,'canceled',
            :amount_cents,:reason,:params_fingerprint,:idempotency_key,:timestamp
        )
        """, record,
    )


def consume_cancel_confirmation(
    conn: sqlite3.Connection, *, context: dict, result_json: str, timestamp: str,
) -> None:
    cursor = conn.execute(
        """
        UPDATE confirmations
        SET status='consumed',approved_at=:timestamp,consumed_at=:timestamp,result_json=:result_json
        WHERE confirmation_id=:confirmation_id AND user_id=:user_id
          AND session_id=:session_id AND thread_id=:thread_id
          AND tool='cancel_order' AND status='pending'
        """,
        {**context, "result_json": result_json, "timestamp": timestamp},
    )
    if cursor.rowcount != 1:
        raise RuntimeError("待确认取消操作状态发生变化")


def get_after_sales_refund_for_withdraw(
    conn: sqlite3.Connection, *, user_id: str, order_id: str, refund_id: str,
) -> dict | None:
    """读取待撤回的售后申请；同时校验订单归属，跨用户返回 None。"""
    row = conn.execute(
        """
        SELECT r.refund_id, r.order_id, r.user_id, r.kind,
               r.amount_cents, r.reason, r.status,
               r.created_at, r.updated_at
        FROM refund_requests AS r
        JOIN orders AS o
          ON o.order_id = r.order_id
         AND o.user_id = r.user_id
        WHERE r.refund_id = ?
          AND r.user_id = ?
          AND r.order_id = ?
          AND r.kind = 'after_sales'
        """,
        (refund_id, user_id, order_id),
    ).fetchone()
    return dict(row) if row is not None else None


def withdraw_refund_in_transaction(
    conn: sqlite3.Connection,
    *,
    refund_id: str,
    user_id: str,
    order_id: str,
    timestamp: str,
) -> None:
    """只允许 pending 变为 withdrawn；调用方负责提交或回滚。"""
    cursor = conn.execute(
        """
        UPDATE refund_requests
        SET status = 'withdrawn', updated_at = ?
        WHERE refund_id = ? AND user_id = ? AND order_id = ?
          AND kind = 'after_sales' AND status = 'pending'
        """,
        (timestamp, refund_id, user_id, order_id),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("撤回时退款申请状态发生变化")


def consume_withdraw_confirmation(
    conn: sqlite3.Connection,
    *,
    context: dict,
    result_json: str,
    timestamp: str,
) -> None:
    """记录撤回执行结果；只允许消费当前上下文的 pending。"""
    cursor = conn.execute(
        """
        UPDATE confirmations
        SET status='consumed', approved_at=:timestamp, consumed_at=:timestamp,
            result_json=:result_json
        WHERE confirmation_id=:confirmation_id AND user_id=:user_id
          AND session_id=:session_id AND thread_id=:thread_id
          AND tool='cancel_refund' AND status='pending'
        """,
        {**context, "result_json": result_json, "timestamp": timestamp},
    )
    if cursor.rowcount != 1:
        raise RuntimeError("待确认撤回操作状态发生变化")
