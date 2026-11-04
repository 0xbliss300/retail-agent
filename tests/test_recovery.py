"""断线后的操作状态查询：已执行、待确认、已结束与跨用户隔离。"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.business import service
from app.harness.confirmation import (
    prepare_cancel_confirmation,
    reject_confirmation,
)
from app.harness.recovery import describe_recovery
from app.main import app
from app.storage import database

NOW = datetime.fromisoformat("2026-11-03T10:00:00+00:00")
CONTEXT = {
    "user_id": "USER-001",
    "session_id": "sess-recover",
    "thread_id": "thread-recover",
}


def dump_database():
    conn = database.get_connection()
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


@pytest.fixture
def recoverable(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO orders (
                    order_id, user_id, status, paid_amount_cents, paid_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "ORD-R1", "USER-001", "paid", 25900,
                    "2026-11-03T09:00:00+00:00",
                ),
            )
    finally:
        conn.close()
    return CONTEXT


def prepare(context, now=NOW):
    pending = prepare_cancel_confirmation(
        **context, order_id="ORD-R1", reason="不想要了", now=now,
    )
    assert pending["status"] == "awaiting_confirmation"
    return pending["confirmation_id"]


def test_without_any_operation_returns_idle(recoverable):
    result = describe_recovery(**recoverable, now=NOW)

    assert result["ok"] is True
    assert result["state"] == "idle"
    assert result["last_state"] is None
    assert result["result"] is None


def test_pending_confirmation_is_reported(recoverable):
    confirmation_id = prepare(recoverable)

    result = describe_recovery(**recoverable, now=NOW)

    assert result["state"] == "awaiting_confirmation"
    assert result["last_state"] == "pending"
    assert result["tool"] == "cancel_order"
    assert result["order_id"] == "ORD-R1"
    assert result["expires_at"] is not None
    assert confirmation_id


def test_executed_operation_returns_saved_result(recoverable):
    confirmation_id = prepare(recoverable)
    approved = service.approve_cancel_confirmation(
        **recoverable, confirmation_id=confirmation_id, now=NOW,
    )
    assert approved["status"] == "canceled"

    result = describe_recovery(**recoverable, now=NOW + timedelta(minutes=30))

    assert result["state"] == "executed"
    assert result["last_state"] == "consumed"
    assert result["result"]["status"] == "canceled"
    assert result["result"]["order_id"] == "ORD-R1"
    assert result["result"]["refund"]["amount_cents"] == 25900


def test_rejected_operation_returns_idle(recoverable):
    confirmation_id = prepare(recoverable)
    reject_confirmation(**recoverable, confirmation_id=confirmation_id, now=NOW)

    result = describe_recovery(**recoverable, now=NOW)

    assert result["state"] == "idle"
    assert result["last_state"] == "rejected"
    assert result["result"] is None


def test_expired_pending_is_reported_as_expired(recoverable):
    prepare(recoverable)

    result = describe_recovery(**recoverable, now=NOW + timedelta(minutes=11))

    assert result["state"] == "idle"
    assert result["last_state"] == "expired"


def test_query_is_read_only(recoverable):
    prepare(recoverable)
    before = dump_database()

    describe_recovery(**recoverable, now=NOW)
    describe_recovery(**recoverable, now=NOW + timedelta(minutes=11))

    assert dump_database() == before


def test_other_user_cannot_see_operation(recoverable):
    prepare(recoverable)

    result = describe_recovery(
        user_id="USER-002",
        session_id=recoverable["session_id"],
        thread_id=recoverable["thread_id"],
        now=NOW,
    )

    assert result["state"] == "idle"
    assert result["last_state"] is None


def test_http_recover_reports_executed(recoverable):
    confirmation_id = prepare(recoverable)
    service.approve_cancel_confirmation(
        **recoverable, confirmation_id=confirmation_id, now=NOW,
    )
    client = TestClient(app)

    response = client.post(
        "/recover",
        json={
            "session_id": recoverable["session_id"],
            "thread_id": recoverable["thread_id"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "executed"
    assert body["result"]["status"] == "canceled"


def test_http_recover_reports_pending(recoverable):
    # 接口用真实时钟判断是否过期，因此这里按当前时间构造订单与确认记录。
    now = datetime.now(UTC)
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO orders (
                    order_id, user_id, status, paid_amount_cents, paid_at
                )
                VALUES ('ORD-R2', 'USER-001', 'paid', 9900, ?)
                """,
                ((now - timedelta(hours=1)).isoformat(),),
            )
    finally:
        conn.close()
    prepare_cancel_confirmation(
        **recoverable, order_id="ORD-R2", reason="不想要了", now=now,
    )
    client = TestClient(app)

    response = client.post(
        "/recover",
        json={
            "session_id": recoverable["session_id"],
            "thread_id": recoverable["thread_id"],
        },
    )

    assert response.status_code == 200
    assert response.json()["state"] == "awaiting_confirmation"


def test_http_recover_rejects_empty_context(recoverable):
    client = TestClient(app)

    response = client.post(
        "/recover", json={"session_id": "", "thread_id": "t"},
    )

    assert response.status_code == 422
