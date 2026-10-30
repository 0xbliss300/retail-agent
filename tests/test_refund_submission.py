import json
import sqlite3
from datetime import datetime, timedelta

import pytest
from openai.types.chat import ChatCompletionMessage

from app.business import service
from app.harness.runtime import create_messages, run_agent
from app.harness.confirmation import (
    prepare_refund_confirmation,
    reject_confirmation,
)
from app.storage import database


@pytest.fixture
def pending_refund(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()

    now = datetime.fromisoformat("2026-10-30T02:00:00+00:00")
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO orders (
                    order_id, user_id, status,
                    paid_amount_cents, shipped_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "ORD-SUBMIT", "USER-SUBMIT", "shipped",
                    8900, "2026-10-29T02:00:00+00:00",
                ),
            )
    finally:
        conn.close()

    context = {
        "user_id": "USER-SUBMIT",
        "session_id": "SESSION-SUBMIT",
        "thread_id": "THREAD-SUBMIT",
    }
    pending = prepare_refund_confirmation(
        **context,
        order_id="ORD-SUBMIT",
        reason="商品破损",
        now=now,
    )
    assert pending["status"] == "awaiting_confirmation"

    return {
        **context,
        "confirmation_id": pending["confirmation_id"],
    }, now


def read_state(context):
    conn = database.get_connection()
    try:
        refunds = [
            dict(row)
            for row in conn.execute("SELECT * FROM refund_requests")
        ]
        confirmation = dict(conn.execute(
            "SELECT * FROM confirmations WHERE confirmation_id = ?",
            (context["confirmation_id"],),
        ).fetchone())
        order = dict(conn.execute(
            "SELECT * FROM orders WHERE order_id = 'ORD-SUBMIT'"
        ).fetchone())
        return refunds, confirmation, order
    finally:
        conn.close()


def test_submit_and_repeat_return_same_refund(pending_refund):
    context, now = pending_refund

    result = service.approve_refund_confirmation(**context, now=now)
    assert result["ok"] is True
    assert result["status"] == "submitted"

    # 已经完成的操作，即使超过原确认有效期，也应返回原结果。
    repeated = service.approve_refund_confirmation(
        **context, now=now + timedelta(minutes=20),
    )
    assert repeated == result

    refunds, confirmation, order = read_state(context)
    assert len(refunds) == 1
    assert refunds[0]["refund_id"] == result["refund_id"]
    assert refunds[0]["amount_cents"] == 8900
    assert refunds[0]["reason"] == "商品破损"
    assert refunds[0]["status"] == "pending"
    assert refunds[0]["confirmation_id"] == context["confirmation_id"]
    assert confirmation["status"] == "consumed"
    assert order["status"] == "shipped"


@pytest.mark.parametrize("field", ["user_id", "session_id", "thread_id"])
def test_wrong_context_cannot_submit(pending_refund, field):
    context, now = pending_refund
    wrong_context = {**context, field: "WRONG"}

    result = service.approve_refund_confirmation(
        **wrong_context, now=now,
    )
    assert result["error"] == "CONFIRMATION_NOT_FOUND"

    refunds, confirmation, _ = read_state(context)
    assert refunds == []
    assert confirmation["status"] == "pending"


def test_exact_expiry_cannot_submit(pending_refund):
    context, now = pending_refund

    result = service.approve_refund_confirmation(
        **context, now=now + timedelta(minutes=10),
    )
    assert result["error"] == "CONFIRMATION_EXPIRED"

    refunds, confirmation, _ = read_state(context)
    assert refunds == []
    assert confirmation["status"] == "expired"


def test_failure_consuming_confirmation_rolls_back_refund(pending_refund):
    context, now = pending_refund

    # 用真实 SQLite 故障验证：申请插入后，消费确认失败。
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                CREATE TRIGGER fail_consumption
                BEFORE UPDATE OF status ON confirmations
                WHEN NEW.status = 'consumed'
                BEGIN
                    SELECT RAISE(ABORT, 'test consume failure');
                END
                """
            )
    finally:
        conn.close()

    with pytest.raises(sqlite3.IntegrityError, match="test consume failure"):
        service.approve_refund_confirmation(**context, now=now)

    refunds, confirmation, _ = read_state(context)
    assert refunds == []
    assert confirmation["status"] == "pending"


def test_rejected_confirmation_cannot_submit(pending_refund):
    context, now = pending_refund
    rejected = reject_confirmation(**context, now=now)
    assert rejected["status"] == "rejected"
    before = read_state(context)

    result = service.approve_refund_confirmation(**context, now=now)

    assert result["ok"] is False
    assert result["error"] == "CONFIRMATION_NOT_PENDING"
    assert read_state(context) == before


@pytest.mark.parametrize(
    "sql,value,expected_error,policy_code",
    [
        (
            "UPDATE orders SET status = ? WHERE order_id = 'ORD-SUBMIT'",
            "canceled", "POLICY_DENIED", "STATUS_NOT_SUPPORTED",
        ),
        (
            "UPDATE orders SET shipped_at = ? WHERE order_id = 'ORD-SUBMIT'",
            "2026-10-20T02:00:00+00:00", "POLICY_DENIED", "WINDOW_EXPIRED",
        ),
        (
            "UPDATE orders SET paid_amount_cents = ? WHERE order_id = 'ORD-SUBMIT'",
            9900, "CONFIRMATION_STALE", None,
        ),
    ],
)
def test_changed_order_cannot_submit(
    pending_refund, sql, value, expected_error, policy_code,
):
    context, now = pending_refund
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(sql, (value,))
    finally:
        conn.close()

    before = read_state(context)
    result = service.approve_refund_confirmation(**context, now=now)

    assert result["ok"] is False
    assert result["error"] == expected_error
    if policy_code is not None:
        assert result["policy_code"] == policy_code
    assert read_state(context) == before
    refunds, confirmation, _ = read_state(context)
    assert refunds == []
    assert confirmation["status"] == "pending"


def test_another_thread_submitted_refund_cannot_duplicate(pending_refund):
    context, now = pending_refund
    other_context = {
        "user_id": context["user_id"],
        "session_id": context["session_id"],
        "thread_id": "THREAD-OTHER",
    }
    other_pending = prepare_refund_confirmation(
        **other_context, order_id="ORD-SUBMIT", reason="商品破损", now=now,
    )
    assert other_pending["status"] == "awaiting_confirmation"
    other_context["confirmation_id"] = other_pending["confirmation_id"]

    first = service.approve_refund_confirmation(**context, now=now)
    assert first["ok"] is True
    before = read_state(other_context)

    result = service.approve_refund_confirmation(**other_context, now=now)

    assert result["ok"] is False
    assert result["error"] == "POLICY_DENIED"
    assert result["policy_code"] == "ACTIVE_REFUND_EXISTS"
    assert read_state(other_context) == before
    refunds, confirmation, _ = read_state(other_context)
    assert len(refunds) == 1
    assert refunds[0]["refund_id"] == first["refund_id"]
    assert confirmation["status"] == "pending"


def test_changed_confirmation_params_cannot_submit(pending_refund):
    context, now = pending_refund
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                UPDATE confirmations
                SET params_json = replace(params_json, ?, ?)
                WHERE confirmation_id = ?
                """,
                ("商品破损", "不想要了", context["confirmation_id"]),
            )
    finally:
        conn.close()

    before = read_state(context)
    # 确保测试确实改变了保存的原因。
    assert "不想要了" in before[1]["params_json"]
    result = service.approve_refund_confirmation(**context, now=now)

    assert result["ok"] is False
    assert result["error"] == "CONFIRMATION_INVALID"
    assert read_state(context) == before


def install_intent_report(monkeypatch, arguments, *, duplicate=False):
    """仅替换联网模型响应，Runtime、Service 与 SQLite 仍真实执行。"""
    call = {
        "id": "call_intent_test",
        "type": "function",
        "function": {
            "name": "report_confirmation_intent",
            "arguments": arguments,
        },
    }
    calls = [call]
    if duplicate:
        calls.append({**call, "id": "call_intent_duplicate"})
    response = ChatCompletionMessage(
        role="assistant", content=None, tool_calls=calls,
    )
    monkeypatch.setattr(
        "app.agent.confirmation_intent.request_model",
        lambda client, messages, tools: response,
    )


@pytest.mark.parametrize(
    "scenario,user_message,intent,evidence,expected_status,refund_count",
    [
        ("approve", "确认提交", "approve", "确认提交", "consumed", 1),
        ("reject", "算了不申请了", "reject", "不申请了", "rejected", 0),
        ("question", "确认后会怎样", "other", "确认后会怎样", "pending", 0),
        ("bad_evidence", "先等等", "approve", "确认提交", "pending", 0),
        ("extra_fields", "确认提交", "approve", "确认提交", "pending", 0),
        ("malformed", "确认提交", "approve", "确认提交", "pending", 0),
        ("multiple", "确认提交", "approve", "确认提交", "pending", 0),
    ],
)
def test_chat_decision_controls_saved_confirmation(
    pending_refund, monkeypatch, scenario, user_message,
    intent, evidence, expected_status, refund_count,
):
    context, now = pending_refund
    report = {"intent": intent, "evidence": evidence}
    if scenario == "extra_fields":
        report["confirmation_id"] = context["confirmation_id"]
    arguments = "not json" if scenario == "malformed" else json.dumps(report)
    install_intent_report(monkeypatch, arguments, duplicate=scenario == "multiple")

    messages = create_messages("离线测试")
    answer = run_agent(
        user_message, context["user_id"], messages, None,
        session_id=context["session_id"], thread_id=context["thread_id"], now=now,
    )

    refunds, confirmation, order = read_state(context)
    assert confirmation["status"] == expected_status
    assert len(refunds) == refund_count
    assert order["status"] == "shipped"
    assert messages[-1] == {"role": "assistant", "content": answer}
    if refund_count:
        assert refunds[0]["confirmation_id"] == context["confirmation_id"]
        assert refunds[0]["amount_cents"] == 8900
        assert refunds[0]["status"] == "pending"
        assert refunds[0]["refund_id"] in answer


def test_chat_approval_reports_policy_denial_without_writing(pending_refund, monkeypatch):
    context, now = pending_refund
    conn = database.get_connection()
    try:
        with conn:
            conn.execute("UPDATE orders SET status = 'canceled' WHERE order_id = 'ORD-SUBMIT'")
    finally:
        conn.close()
    before = read_state(context)
    install_intent_report(
        monkeypatch, json.dumps({"intent": "approve", "evidence": "确认提交"}),
    )

    answer = run_agent(
        "确认提交", context["user_id"], create_messages("离线测试"), None,
        session_id=context["session_id"], thread_id=context["thread_id"], now=now,
    )

    assert "POLICY_DENIED" in answer
    assert read_state(context) == before
