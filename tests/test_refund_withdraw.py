"""撤回尚未处理的售后退款申请：状态、幂等、与结算的竞争。"""

import json
from datetime import datetime, timedelta

import pytest
from openai.types.chat import ChatCompletionMessage

from app.business import service
from app.harness.confirmation import (
    prepare_refund_confirmation,
    prepare_withdraw_confirmation,
    reject_confirmation,
)
from app.harness.runtime import create_messages, run_agent
from app.storage import database

ORDER_ID = "ORD-WITHDRAW"
REFUND_ID = "rf_WITHDRAW0001"
NOW = datetime.fromisoformat("2026-10-30T02:00:00+00:00")


def _insert_pending_refund(order_id: str = ORDER_ID, refund_id: str = REFUND_ID) -> None:
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO refund_requests (
                    refund_id, order_id, user_id, kind, amount_cents, reason,
                    status, idempotency_key, confirmation_id, created_at, updated_at
                )
                VALUES (?, ?, 'USER-WITHDRAW', 'after_sales', 8900, '商品破损',
                        'pending', NULL, NULL, ?, ?)
                """,
                (refund_id, order_id, NOW.isoformat(), NOW.isoformat()),
            )
    finally:
        conn.close()


@pytest.fixture
def pending_withdraw(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()

    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO orders (
                    order_id, user_id, status, paid_amount_cents, shipped_at
                )
                VALUES (?, 'USER-WITHDRAW', 'shipped', 8900, ?)
                """,
                (ORDER_ID, "2026-10-29T02:00:00+00:00"),
            )
    finally:
        conn.close()

    _insert_pending_refund()

    context = {
        "user_id": "USER-WITHDRAW",
        "session_id": "SESSION-WITHDRAW",
        "thread_id": "THREAD-WITHDRAW",
    }
    pending = prepare_withdraw_confirmation(**context, order_id=ORDER_ID, now=NOW)
    assert pending["status"] == "awaiting_confirmation"
    assert pending["tool"] == "cancel_refund"

    return {**context, "confirmation_id": pending["confirmation_id"]}, NOW


def read_state(context):
    conn = database.get_connection()
    try:
        refund = dict(conn.execute(
            "SELECT * FROM refund_requests WHERE refund_id = ?", (REFUND_ID,)
        ).fetchone())
        confirmation = dict(conn.execute(
            "SELECT * FROM confirmations WHERE confirmation_id = ?",
            (context["confirmation_id"],),
        ).fetchone())
        order = dict(conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (ORDER_ID,)
        ).fetchone())
        return refund, confirmation, order
    finally:
        conn.close()


def test_withdraw_marks_refund_and_keeps_order(pending_withdraw):
    context, now = pending_withdraw

    result = service.approve_withdraw_confirmation(**context, now=now)

    assert result["ok"] is True
    assert result["status"] == "withdrawn"
    assert result["refund_id"] == REFUND_ID
    assert result["amount_cents"] == 8900

    refund, confirmation, order = read_state(context)
    assert refund["status"] == "withdrawn"
    assert refund["reason"] == "商品破损"
    assert refund["amount_cents"] == 8900
    assert confirmation["status"] == "consumed"
    assert json.loads(confirmation["result_json"]) == result
    # 撤回只结束申请，不改变订单生命周期。
    assert order["status"] == "shipped"


def test_repeat_withdraw_returns_saved_result(pending_withdraw):
    context, now = pending_withdraw
    first = service.approve_withdraw_confirmation(**context, now=now)
    before = read_state(context)

    repeated = service.approve_withdraw_confirmation(
        **context, now=now + timedelta(minutes=20),
    )

    assert repeated == first
    assert read_state(context) == before


def test_withdrawn_refund_allows_new_application(pending_withdraw):
    context, now = pending_withdraw
    service.approve_withdraw_confirmation(**context, now=now)

    new_context = {
        "user_id": context["user_id"],
        "session_id": context["session_id"],
        "thread_id": "THREAD-RETRY",
    }
    retry = prepare_refund_confirmation(
        **new_context, order_id=ORDER_ID, reason="仍然想退", now=now,
    )

    assert retry["status"] == "awaiting_confirmation"
    assert retry["ok"] is True


def test_withdrawn_refund_cannot_be_settled(pending_withdraw):
    context, now = pending_withdraw
    service.approve_withdraw_confirmation(**context, now=now)

    result = service.settle_refund(refund_id=REFUND_ID, now=now)

    assert result["ok"] is False
    assert result["error"] == "REFUND_NOT_PENDING"
    assert result["status"] == "withdrawn"
    _, _, order = read_state(context)
    assert order["status"] == "shipped"


def test_settled_refund_cannot_be_withdrawn(pending_withdraw):
    context, now = pending_withdraw
    settled = service.settle_refund(refund_id=REFUND_ID, now=now)
    assert settled["ok"] is True
    before = read_state(context)

    result = service.approve_withdraw_confirmation(**context, now=now)

    assert result["ok"] is False
    assert result["error"] == "REFUND_NOT_PENDING"
    assert result["status"] == "completed"
    assert read_state(context) == before


@pytest.mark.parametrize("field", ["user_id", "session_id", "thread_id"])
def test_wrong_context_cannot_withdraw(pending_withdraw, field):
    context, now = pending_withdraw
    before = read_state(context)

    result = service.approve_withdraw_confirmation(
        **{**context, field: "WRONG"}, now=now,
    )

    assert result["error"] == "CONFIRMATION_NOT_FOUND"
    assert read_state(context) == before


def test_expired_withdraw_cannot_execute(pending_withdraw):
    context, now = pending_withdraw
    before = read_state(context)

    result = service.approve_withdraw_confirmation(
        **context, now=now + timedelta(minutes=11),
    )

    assert result["error"] == "CONFIRMATION_EXPIRED"
    refund, confirmation, _ = read_state(context)
    assert refund["status"] == "pending"
    assert confirmation["status"] == "expired"
    assert before[2]["status"] == "shipped"


def test_reject_keeps_refund_pending(pending_withdraw):
    context, now = pending_withdraw

    result = reject_confirmation(**context, now=now)

    assert result == {"ok": True, "status": "rejected"}
    refund, confirmation, _ = read_state(context)
    assert refund["status"] == "pending"
    assert confirmation["status"] == "rejected"


def test_order_without_refund_cannot_prepare_withdraw(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO orders (order_id, user_id, status, paid_amount_cents)
                VALUES ('ORD-NOREFUND', 'USER-WITHDRAW', 'shipped', 8900)
                """
            )
    finally:
        conn.close()

    result = prepare_withdraw_confirmation(
        user_id="USER-WITHDRAW",
        session_id="S",
        thread_id="T",
        order_id="ORD-NOREFUND",
        now=NOW,
    )

    assert result["ok"] is False
    assert result["error"] == "NO_ACTIVE_REFUND"


def install_withdraw_call(monkeypatch, arguments):
    """只替换联网模型响应；Runtime、工具执行与 SQLite 仍真实执行。"""
    response = ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[{
            "id": "call_withdraw",
            "type": "function",
            "function": {"name": "withdraw_refund", "arguments": arguments},
        }],
    )
    monkeypatch.setattr(
        "app.harness.runtime.request_model",
        lambda client, messages, tools: response,
    )


def test_model_tool_only_prepares_withdraw(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO orders (order_id, user_id, status, paid_amount_cents, shipped_at)
                VALUES (?, 'USER-WITHDRAW', 'shipped', 8900, ?)
                """,
                (ORDER_ID, "2026-10-29T02:00:00+00:00"),
            )
    finally:
        conn.close()
    _insert_pending_refund()

    install_withdraw_call(monkeypatch, json.dumps({"order_id": ORDER_ID}))
    messages = create_messages("离线测试")
    answer = run_agent(
        "我想撤回这个退款申请",
        "USER-WITHDRAW",
        messages,
        None,
        session_id="SESSION-MODEL",
        thread_id="THREAD-MODEL",
        now=NOW,
    )

    assert "尚未撤回" in answer
    conn = database.get_connection()
    try:
        refund = conn.execute(
            "SELECT status FROM refund_requests WHERE refund_id = ?", (REFUND_ID,)
        ).fetchone()
        confirmation = conn.execute(
            "SELECT tool, status FROM confirmations WHERE thread_id = 'THREAD-MODEL'"
        ).fetchone()
    finally:
        conn.close()
    # 准备阶段只能生成待确认记录，不能直接改申请。
    assert refund["status"] == "pending"
    assert confirmation["tool"] == "cancel_refund"
    assert confirmation["status"] == "pending"
