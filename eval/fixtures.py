"""评测使用的隔离数据库与固定时钟数据。"""

import io
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.storage import database
from eval.case_schema import EvalCase

FIXED_NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)


@contextmanager
def isolated_database(db_path: Path) -> Iterator[None]:
    """把业务数据库切到临时文件，退出时恢复原路径。"""
    original = database.DB_PATH
    database.DB_PATH = db_path
    try:
        with redirect_stdout(io.StringIO()):
            database.init_db()
        yield
    finally:
        database.DB_PATH = original


def seed_order(*, user_id: str, order: dict) -> None:
    """写入一笔用例订单；固定时钟保证付款和发货时间可重复。"""
    paid_at = None
    if order["status"] != "unpaid":
        paid_at = (FIXED_NOW - timedelta(minutes=order["paid_minutes_ago"])).isoformat()
    shipped_at = None
    if order["status"] == "shipped":
        shipped_at = (FIXED_NOW - timedelta(minutes=order["shipped_minutes_ago"])).isoformat()

    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO orders (
                    order_id, user_id, status, paid_amount_cents, paid_at, shipped_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    order["order_id"],
                    user_id,
                    order["status"],
                    order["paid_amount_cents"],
                    paid_at,
                    shipped_at,
                ),
            )
    finally:
        conn.close()


def seed_existing_refund(case: EvalCase) -> None:
    """播种用例预置的售后申请；时间与金额由固定时钟和订单决定。"""
    refund = case.existing_refund
    if refund is None:
        return
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO refund_requests (
                    refund_id, order_id, user_id, kind, amount_cents, reason,
                    status, idempotency_key, confirmation_id, created_at, updated_at
                )
                VALUES (?, ?, ?, 'after_sales', ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    f"rf_{case.case_id}",
                    case.order["order_id"],
                    case.user_id,
                    refund["amount_cents"],
                    refund["reason"],
                    refund["status"],
                    FIXED_NOW.isoformat(),
                    FIXED_NOW.isoformat(),
                ),
            )
    finally:
        conn.close()


def seed_case_order(case: EvalCase) -> None:
    seed_order(user_id=case.user_id, order=case.order)
    seed_existing_refund(case)
