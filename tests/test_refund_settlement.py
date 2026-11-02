"""退款结算：原子写入、重复调用重放和只追加记录。"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.business import service
from app.harness.confirmation import prepare_refund_confirmation
from app.storage import database

NOW = datetime(2026, 10, 30, 2, tzinfo=UTC)
USER_ID = "USER-SETTLE"
ORDER_ID = "ORD-SETTLE"


@pytest.fixture
def pending_settlement(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()

    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO orders (
                    order_id, user_id, status, paid_amount_cents, paid_at, shipped_at
                )
                VALUES (?, ?, 'shipped', ?, ?, ?)
                """,
                (
                    ORDER_ID,
                    USER_ID,
                    8900,
                    "2026-10-29T02:00:00+00:00",
                    "2026-10-29T02:00:00+00:00",
                ),
            )
    finally:
        conn.close()

    context = {
        "user_id": USER_ID,
        "session_id": "SESSION-SETTLE",
        "thread_id": "THREAD-SETTLE",
    }
    prepared = prepare_refund_confirmation(
        **context,
        order_id=ORDER_ID,
        reason="商品破损",
        now=NOW,
    )
    assert prepared["status"] == "awaiting_confirmation", prepared
    submitted = service.approve_refund_confirmation(
        **context,
        confirmation_id=prepared["confirmation_id"],
        now=NOW,
    )
    assert submitted["ok"] is True, submitted
    return submitted["refund_id"]


def read_state(refund_id):
    conn = database.get_connection()
    try:
        refund = dict(
            conn.execute(
                "SELECT * FROM refund_requests WHERE refund_id = ?",
                (refund_id,),
            ).fetchone()
        )
        order = dict(
            conn.execute(
                "SELECT * FROM orders WHERE order_id = ?",
                (refund["order_id"],),
            ).fetchone()
        )
        settlements = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM refund_settlements WHERE refund_id = ?",
                (refund_id,),
            )
        ]
        return refund, order, settlements
    finally:
        conn.close()


def dump_database():
    conn = database.get_connection()
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def test_settlement_completes_refund_and_order(pending_settlement):
    result = service.settle_refund(
        refund_id=pending_settlement,
        now=NOW + timedelta(minutes=1),
    )

    assert result == {
        "ok": True,
        "refund_id": pending_settlement,
        "order_id": ORDER_ID,
        "amount_cents": 8900,
        "refund_status": "completed",
        "order_status": "refunded",
        "replayed": False,
    }
    refund, order, settlements = read_state(pending_settlement)
    assert refund["status"] == "completed"
    assert refund["amount_cents"] == 8900
    assert refund["reason"] == "商品破损"
    assert order["status"] == "refunded"
    assert len(settlements) == 1
    assert settlements[0]["previous_refund_status"] == "pending"
    assert settlements[0]["new_refund_status"] == "completed"
    assert settlements[0]["previous_order_status"] == "shipped"
    assert settlements[0]["new_order_status"] == "refunded"

    # 聊天侧读取订单和退款进度时应看到结算后的状态。
    assert service.get_refund_status(
        user_id=USER_ID, order_id=ORDER_ID,
    )["refund"]["status"] == "completed"
    assert service.get_order(
        user_id=USER_ID, order_id=ORDER_ID,
    )["order"]["status"] == "refunded"


def test_repeated_settlement_replays_saved_result_without_writes(pending_settlement):
    first = service.settle_refund(refund_id=pending_settlement, now=NOW)
    before = dump_database()

    repeated = service.settle_refund(
        refund_id=pending_settlement,
        now=NOW + timedelta(days=1),
    )

    assert repeated == {**first, "replayed": True}
    assert dump_database() == before


def test_cancellation_refund_cannot_be_settled(pending_settlement):
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO refund_requests (
                    refund_id, order_id, user_id, kind,
                    amount_cents, reason, status, created_at, updated_at
                )
                VALUES (?, ?, ?, 'cancellation', ?, ?, 'completed', ?, ?)
                """,
                (
                    "rf_cancel",
                    ORDER_ID,
                    USER_ID,
                    8900,
                    "取消退款",
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )
    finally:
        conn.close()

    before = dump_database()
    result = service.settle_refund(refund_id="rf_cancel", now=NOW)

    assert result["error"] == "REFUND_KIND_NOT_SUPPORTED"
    assert dump_database() == before


def test_missing_and_not_pending_refunds_are_rejected(pending_settlement):
    assert service.settle_refund(refund_id="rf_missing", now=NOW)["error"] == "REFUND_NOT_FOUND"

    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE refund_requests SET status = 'rejected' WHERE refund_id = ?",
                (pending_settlement,),
            )
    finally:
        conn.close()

    before = dump_database()
    result = service.settle_refund(refund_id=pending_settlement, now=NOW)

    assert result["error"] == "REFUND_NOT_PENDING"
    assert dump_database() == before


def test_amount_mismatch_is_rejected_without_writes(pending_settlement):
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE orders SET paid_amount_cents = 9000 WHERE order_id = ?",
                (ORDER_ID,),
            )
    finally:
        conn.close()

    before = dump_database()
    result = service.settle_refund(refund_id=pending_settlement, now=NOW)

    assert result["error"] == "AMOUNT_MISMATCH"
    assert dump_database() == before


def test_order_must_still_be_shipped_or_delivered(pending_settlement):
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE orders SET status = 'canceled' WHERE order_id = ?",
                (ORDER_ID,),
            )
    finally:
        conn.close()

    before = dump_database()
    result = service.settle_refund(refund_id=pending_settlement, now=NOW)

    assert result["error"] == "ORDER_STATUS_NOT_SUPPORTED"
    assert dump_database() == before


def test_failure_inserting_settlement_rolls_back_refund_and_order(pending_settlement):
    conn = database.get_connection()
    try:
        conn.execute(
            "CREATE TRIGGER settlement_failure BEFORE INSERT ON refund_settlements "
            "BEGIN SELECT RAISE(ABORT, 'test settlement failure'); END"
        )
    finally:
        conn.close()

    before = dump_database()
    with pytest.raises(sqlite3.IntegrityError, match="test settlement failure"):
        service.settle_refund(refund_id=pending_settlement, now=NOW)
    assert dump_database() == before


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE refund_settlements SET amount_cents = 0",
        "DELETE FROM refund_settlements",
    ],
)
def test_settlement_log_is_append_only(pending_settlement, sql):
    assert service.settle_refund(refund_id=pending_settlement, now=NOW)["ok"] is True
    before = dump_database()

    conn = database.get_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError), conn:
            conn.execute(sql)
    finally:
        conn.close()

    assert dump_database() == before