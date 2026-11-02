"""HTTP /confirm：确定性批准、拒绝、重复重放和上下文校验。"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import main
from app.harness.confirmation import (
    prepare_cancel_confirmation,
    prepare_refund_confirmation,
)
from app.storage import database, persistence

USER_ID = "USER-001"
SESSION_ID = "sess_confirm"
THREAD_ID = "thread_confirm"


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()
    monkeypatch.setattr(main, "DEMO_USER_ID", USER_ID)

    def forbidden(*args, **kwargs):
        raise AssertionError("/confirm 不应调用模型")

    monkeypatch.setattr(main, "create_client", forbidden)
    monkeypatch.setattr("app.harness.runtime.request_model", forbidden)
    monkeypatch.setattr("app.harness.runtime.stream_model", forbidden)
    monkeypatch.setattr("app.agent.confirmation_intent.request_model", forbidden)

    with TestClient(main.app) as client:
        yield client


def insert_order(order_id, status, *, paid_amount=0, shipped_at=None):
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO orders (
                    order_id, user_id, status, paid_amount_cents, shipped_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (order_id, USER_ID, status, paid_amount, shipped_at),
            )
    finally:
        conn.close()


def prepare_cancel(order_id):
    result = prepare_cancel_confirmation(
        user_id=USER_ID,
        session_id=SESSION_ID,
        thread_id=THREAD_ID,
        order_id=order_id,
        reason="不需要了",
        now=datetime.now(UTC),
    )
    assert result["status"] == "awaiting_confirmation", result
    return result["confirmation_id"]


def prepare_refund(order_id):
    result = prepare_refund_confirmation(
        user_id=USER_ID,
        session_id=SESSION_ID,
        thread_id=THREAD_ID,
        order_id=order_id,
        reason="商品破损",
        now=datetime.now(UTC),
    )
    assert result["status"] == "awaiting_confirmation", result
    return result["confirmation_id"]


def confirm(client, confirmation_id, decision):
    return client.post(
        "/confirm",
        json={
            "session_id": SESSION_ID,
            "thread_id": THREAD_ID,
            "confirmation_id": confirmation_id,
            "decision": decision,
        },
    )


def read_database(query, parameters=()):
    conn = database.get_connection()
    try:
        return [dict(row) for row in conn.execute(query, parameters)]
    finally:
        conn.close()


def dump_database():
    conn = database.get_connection()
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def test_approve_cancel_then_repeat_replays_without_writes(api_client):
    order_id = "ORD-CONFIRM-1"
    insert_order(order_id, "unpaid")
    confirmation_id = prepare_cancel(order_id)

    first = confirm(api_client, confirmation_id, "approve")
    assert first.status_code == 200
    body = first.json()
    assert "已取消" in body["reply"]
    assert body["pending_action"] is None
    assert body["history_saved"] is True
    assert read_database(
        "SELECT status FROM orders WHERE order_id = ?", (order_id,)
    )[0]["status"] == "canceled"
    assert read_database(
        "SELECT status FROM confirmations WHERE confirmation_id = ?",
        (confirmation_id,),
    )[0]["status"] == "consumed"
    assert len(read_database("SELECT * FROM operation_logs")) == 1
    assert read_database("SELECT * FROM refund_requests") == []

    rows = read_database("SELECT * FROM chat_threads")
    assert len(rows) == 1
    assert rows[0]["version"] == 1

    before = dump_database()
    second = confirm(api_client, confirmation_id, "approve")
    assert second.status_code == 200
    assert second.json()["reply"] == body["reply"]
    assert dump_database() == before


def test_reject_cancel_does_not_modify_business_data(api_client):
    order_id = "ORD-CONFIRM-REJECT"
    insert_order(order_id, "unpaid")
    confirmation_id = prepare_cancel(order_id)

    response = confirm(api_client, confirmation_id, "reject")
    assert response.status_code == 200
    assert "已拒绝" in response.json()["reply"]
    assert read_database(
        "SELECT status FROM orders WHERE order_id = ?", (order_id,)
    )[0]["status"] == "unpaid"
    assert read_database(
        "SELECT status FROM confirmations WHERE confirmation_id = ?",
        (confirmation_id,),
    )[0]["status"] == "rejected"
    assert read_database("SELECT * FROM operation_logs") == []
    assert read_database("SELECT * FROM refund_requests") == []


def test_approve_refund_creates_pending_request(api_client):
    order_id = "ORD-CONFIRM-REFUND"
    now = datetime.now(UTC)
    insert_order(
        order_id,
        "shipped",
        paid_amount=8900,
        shipped_at=(now - timedelta(days=1)).isoformat(),
    )
    confirmation_id = prepare_refund(order_id)

    response = confirm(api_client, confirmation_id, "approve")
    assert response.status_code == 200
    assert "退款申请已提交" in response.json()["reply"]
    refunds = read_database("SELECT * FROM refund_requests")
    assert len(refunds) == 1
    assert refunds[0]["status"] == "pending"
    assert refunds[0]["amount_cents"] == 8900
    assert read_database(
        "SELECT status FROM orders WHERE order_id = ?", (order_id,)
    )[0]["status"] == "shipped"
    assert read_database(
        "SELECT status FROM confirmations WHERE confirmation_id = ?",
        (confirmation_id,),
    )[0]["status"] == "consumed"


def test_missing_confirmation_is_rejected_without_writes(api_client):
    order_id = "ORD-CONFIRM-MISSING"
    insert_order(order_id, "unpaid")
    prepare_cancel(order_id)
    before = dump_database()

    response = confirm(api_client, "cfm_missing", "approve")

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "CONFIRMATION_NOT_FOUND"
    assert dump_database() == before


def test_wrong_session_cannot_confirm(api_client):
    order_id = "ORD-CONFIRM-CONTEXT"
    insert_order(order_id, "unpaid")
    confirmation_id = prepare_cancel(order_id)

    response = api_client.post(
        "/confirm",
        json={
            "session_id": "other_session",
            "thread_id": "other_thread",
            "confirmation_id": confirmation_id,
            "decision": "approve",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "CONVERSATION_NOT_FOUND"
    assert read_database(
        "SELECT status FROM orders WHERE order_id = ?", (order_id,)
    )[0]["status"] == "unpaid"


def test_expired_confirmation_does_not_execute(api_client):
    order_id = "ORD-CONFIRM-EXPIRED"
    insert_order(order_id, "unpaid")
    now = datetime.now(UTC)

    # 保留一条有效聊天快照，让接口能读取到已过期的确认记录。
    persistence.save_conversation(
        user_id=USER_ID,
        session_id=SESSION_ID,
        thread_id=THREAD_ID,
        messages=[
            {"role": "system", "content": "test"},
            {"role": "user", "content": "取消订单"},
            {"role": "assistant", "content": "请确认"},
        ],
        now=now,
        expected_version=0,
    )
    expired = prepare_cancel_confirmation(
        user_id=USER_ID,
        session_id=SESSION_ID,
        thread_id=THREAD_ID,
        order_id=order_id,
        reason="不需要了",
        now=now - timedelta(hours=1),
    )
    assert expired["status"] == "awaiting_confirmation"

    response = confirm(api_client, expired["confirmation_id"], "approve")

    assert response.status_code == 200
    assert "已过期" in response.json()["reply"]
    assert read_database(
        "SELECT status FROM orders WHERE order_id = ?", (order_id,)
    )[0]["status"] == "unpaid"
    assert read_database(
        "SELECT status FROM confirmations WHERE confirmation_id = ?",
        (expired["confirmation_id"],),
    )[0]["status"] == "expired"