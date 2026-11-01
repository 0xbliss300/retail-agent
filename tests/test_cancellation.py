"""取消订单完整链路：规则、确认、原子写入、并发与恢复。"""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from itertools import count
from threading import Barrier

import pytest
from openai.types.chat import ChatCompletionMessage

from app.business import policies, service
from app.harness import confirmation
from app.harness.runtime import create_messages, run_agent
from app.harness.tool_executor import execute_tool
from app.storage import database, seed
from scripts import demo_chat

NOW = datetime(2026, 11, 1, 8, tzinfo=UTC)


@pytest.mark.parametrize(
    "status,paid_at,allowed,code",
    [
        ("unpaid", None, True, "ELIGIBLE"),
        ("paid", NOW - timedelta(hours=1), True, "ELIGIBLE"),
        ("paid", NOW - timedelta(hours=24), True, "ELIGIBLE"),
        ("paid", NOW - timedelta(hours=24, microseconds=1), False, "WINDOW_EXPIRED"),
        ("paid", NOW + timedelta(seconds=1), False, "TIME_IN_FUTURE"),
        ("paid", None, False, "TIME_MISSING"),
        ("paid", "invalid", False, "TIME_INVALID"),
        ("paid", "2026-11-01T07:00:00", False, "TIME_INVALID"),
        ("unpaid", NOW, False, "PAYMENT_STATE_MISMATCH"),
        ("shipped", NOW, False, "STATUS_NOT_SUPPORTED"),
        ("delivered", NOW, False, "STATUS_NOT_SUPPORTED"),
        ("canceled", NOW, False, "STATUS_NOT_SUPPORTED"),
        ("refunded", NOW, False, "STATUS_NOT_SUPPORTED"),
    ],
)
def test_cancel_window_rules(status, paid_at, allowed, code):
    value = paid_at.isoformat() if isinstance(paid_at, datetime) else paid_at
    result = policies.check_cancel_window(status=status, paid_at=value, now=NOW)
    assert result["window_open"] is allowed
    assert result["code"] == code


@pytest.fixture
def cancel_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()
    sequence = count(1)

    def order(
        *,
        status="paid",
        user_id="USER-001",
        amount=None,
        paid_at="default",
        shipped_at=None,
        delivered_at=None,
    ):
        order_id = f"ORD-CANCEL-{next(sequence):03d}"
        if amount is None:
            amount = 0 if status == "unpaid" else 19900
        if paid_at == "default":
            paid_at = None if status == "unpaid" else (NOW - timedelta(hours=1)).isoformat()
        conn = database.get_connection()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO orders VALUES (?,?,?,?,?,?,?)",
                    (order_id, user_id, status, amount, paid_at, shipped_at, delivered_at),
                )
        finally:
            conn.close()
        return order_id

    return order


def prepare(order_id, *, thread_id="thread_cancel", user_id="USER-001", now=NOW):
    context = {"user_id": user_id, "session_id": "sess_cancel", "thread_id": thread_id}
    result = confirmation.prepare_cancel_confirmation(
        **context,
        order_id=order_id,
        reason="不需要了",
        now=now,
    )
    assert result["status"] == "awaiting_confirmation", result
    return {**context, "confirmation_id": result["confirmation_id"]}


def dump_database():
    conn = database.get_connection()
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def state(order_id):
    conn = database.get_connection()
    try:
        return {
            "order": dict(conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()),
            "refunds": [
                dict(r) for r in conn.execute("SELECT * FROM refund_requests WHERE order_id=?", (order_id,))
            ],
            "logs": [
                dict(r) for r in conn.execute("SELECT * FROM operation_logs WHERE order_id=?", (order_id,))
            ],
            "confirmations": [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM confirmations WHERE order_id=? ORDER BY rowid",
                    (order_id,),
                )
            ],
        }
    finally:
        conn.close()


@pytest.mark.parametrize("status,refund_count", [("unpaid", 0), ("paid", 1)])
def test_approval_cancels_order_and_commits_all_results_once(cancel_db, status, refund_count):
    order_id = cancel_db(status=status)
    context = prepare(order_id)
    pending = state(order_id)
    assert pending["order"]["status"] == status
    assert pending["logs"] == pending["refunds"] == []
    assert pending["confirmations"][0]["tool"] == "cancel_order"

    result = service.approve_cancel_confirmation(**context, now=NOW)

    assert result["ok"] is True
    assert result["status"] == "canceled"
    saved = state(order_id)
    assert saved["order"]["status"] == "canceled"
    assert len(saved["refunds"]) == refund_count
    assert len(saved["logs"]) == 1
    assert saved["logs"][0]["previous_status"] == status
    assert saved["logs"][0]["new_status"] == "canceled"
    assert saved["logs"][0]["reason"] == "不需要了"
    record = saved["confirmations"][0]
    assert record["status"] == "consumed"
    assert record["approved_at"] == record["consumed_at"] == NOW.isoformat()
    assert json.loads(record["result_json"]) == result
    if refund_count:
        refund = saved["refunds"][0]
        assert refund["kind"] == "cancellation"
        assert refund["status"] == "completed"
        assert refund["amount_cents"] == 19900
        assert refund["confirmation_id"] == context["confirmation_id"]
        assert refund["idempotency_key"] == f"confirm:{context['confirmation_id']}"
        assert result["refund"]["refund_id"] == refund["refund_id"]
    else:
        assert result["refund"] is None
    before = dump_database()
    assert service.approve_cancel_confirmation(**context, now=NOW + timedelta(days=2)) == result
    assert dump_database() == before


@pytest.mark.parametrize("status", ["shipped", "delivered", "canceled", "refunded"])
def test_unsupported_order_states_do_not_prepare_confirmation(cancel_db, status):
    order_id = cancel_db(status=status)
    before = dump_database()
    result = confirmation.prepare_cancel_confirmation(
        user_id="USER-001",
        session_id="s",
        thread_id="t",
        order_id=order_id,
        reason="不要了",
        now=NOW,
    )
    assert result["ok"] is False
    assert result["error"] == "POLICY_DENIED"
    assert dump_database() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("paid_at", None),
        ("paid_at", "bad"),
        ("paid_at", (NOW - timedelta(hours=25)).isoformat()),
        ("shipped_at", NOW.isoformat()),
        ("delivered_at", NOW.isoformat()),
        ("paid_amount_cents", -1),
    ],
)
def test_invalid_paid_order_information_is_denied(cancel_db, field, value):
    kwargs = {"amount" if field == "paid_amount_cents" else field: value}
    order_id = cancel_db(**kwargs)
    before = dump_database()
    result = service.check_cancel_eligibility(user_id="USER-001", order_id=order_id, now=NOW)
    assert result["ok"] is True and result["eligible"] is False
    assert dump_database() == before


def test_unpaid_order_with_nonzero_payment_is_denied(cancel_db):
    order_id = cancel_db(status="unpaid", amount=100)
    assert (
        service.check_cancel_eligibility(user_id="USER-001", order_id=order_id, now=NOW)["eligible"] is False
    )


@pytest.mark.parametrize(
    "kind,status",
    [
        ("after_sales", "pending"),
        ("after_sales", "approved"),
        ("after_sales", "completed"),
        ("cancellation", "failed"),
        ("cancellation", "completed"),
    ],
)
def test_existing_refund_blocks_duplicate_financial_action(cancel_db, kind, status):
    order_id = cancel_db()
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO refund_requests (
                    refund_id,order_id,user_id,kind,amount_cents,reason,status,created_at,updated_at
                ) VALUES ('existing',?,'USER-001',?,19900,'existing',?,?,?)
                """,
                (order_id, kind, status, NOW.isoformat(), NOW.isoformat()),
            )
    finally:
        conn.close()
    before = dump_database()
    result = service.check_cancel_eligibility(user_id="USER-001", order_id=order_id, now=NOW)
    assert result["eligible"] is False
    assert result["policy_code"] == "REFUND_EXISTS"
    assert dump_database() == before


def test_cutoff_can_expire_while_confirmation_is_still_valid(cancel_db):
    order_id = cancel_db(paid_at=(NOW - timedelta(hours=23, minutes=59)).isoformat())
    context = prepare(order_id)
    before = dump_database()
    result = service.approve_cancel_confirmation(**context, now=NOW + timedelta(minutes=2))
    assert result["error"] == "POLICY_DENIED"
    assert result["policy_code"] == "WINDOW_EXPIRED"
    assert dump_database() == before


def test_cancel_approval_rejects_a_refund_confirmation(cancel_db):
    order_id = cancel_db(status="shipped", shipped_at=(NOW - timedelta(days=1)).isoformat())
    context = {"user_id": "USER-001", "session_id": "s", "thread_id": "t"}
    pending = confirmation.prepare_refund_confirmation(**context, order_id=order_id, reason="破损", now=NOW)
    before = dump_database()
    result = service.approve_cancel_confirmation(
        **context, confirmation_id=pending["confirmation_id"], now=NOW
    )
    assert result["error"] == "CONFIRMATION_INVALID"
    assert dump_database() == before


def test_audit_prevents_recancellation_even_if_order_status_is_reset(cancel_db):
    order_id = cancel_db(status="unpaid")
    assert service.approve_cancel_confirmation(**prepare(order_id), now=NOW)["ok"]
    conn = database.get_connection()
    try:
        with conn:
            conn.execute("UPDATE orders SET status='unpaid' WHERE order_id=?", (order_id,))
    finally:
        conn.close()
    result = service.check_cancel_eligibility(user_id="USER-001", order_id=order_id, now=NOW)
    assert result["eligible"] is False
    assert result["policy_code"] == "ALREADY_CANCELED"


@pytest.mark.parametrize("field", ["user_id", "session_id", "thread_id"])
def test_wrong_confirmation_context_cannot_cancel(cancel_db, field):
    order_id = cancel_db()
    context = prepare(order_id)
    before = dump_database()
    result = service.approve_cancel_confirmation(**{**context, field: "wrong"}, now=NOW)
    assert result == {"ok": False, "error": "CONFIRMATION_NOT_FOUND"}
    assert dump_database() == before


@pytest.mark.parametrize("foreign", [False, True])
def test_preparation_does_not_reveal_unavailable_orders(cancel_db, foreign):
    order_id = cancel_db(user_id="USER-OTHER") if foreign else "MISSING"
    before = dump_database()
    result = confirmation.prepare_cancel_confirmation(
        user_id="USER-001",
        session_id="s",
        thread_id="t",
        order_id=order_id,
        reason="不要了",
        now=NOW,
    )
    assert result == {"ok": False, "error": "ORDER_NOT_FOUND"}
    assert dump_database() == before


@pytest.mark.parametrize("decision", ["reject", "expire"])
def test_rejected_and_expired_operations_do_not_cancel(cancel_db, decision):
    order_id = cancel_db()
    context = prepare(order_id)
    if decision == "reject":
        confirmation.reject_confirmation(**context, now=NOW)
        result = service.approve_cancel_confirmation(**context, now=NOW)
        assert result["error"] == "CONFIRMATION_NOT_PENDING"
    else:
        result = service.approve_cancel_confirmation(**context, now=NOW + timedelta(minutes=10))
        assert result["error"] == "CONFIRMATION_EXPIRED"
    saved = state(order_id)
    assert saved["order"]["status"] == "paid"
    assert saved["refunds"] == saved["logs"] == []
    assert saved["confirmations"][0]["status"] == ("rejected" if decision == "reject" else "expired")


@pytest.mark.parametrize(
    "sql,parameters,error",
    [
        ("UPDATE orders SET status='shipped' WHERE order_id=?", (), "POLICY_DENIED"),
        ("UPDATE orders SET paid_amount_cents=? WHERE order_id=?", (29900,), "CONFIRMATION_STALE"),
        (
            "UPDATE orders SET paid_at=? WHERE order_id=?",
            ((NOW - timedelta(hours=2)).isoformat(),),
            "CONFIRMATION_STALE",
        ),
        (
            "UPDATE orders SET paid_at=? WHERE order_id=?",
            ((NOW - timedelta(hours=25)).isoformat(),),
            "POLICY_DENIED",
        ),
    ],
)
def test_approval_rechecks_current_order(cancel_db, sql, parameters, error):
    order_id = cancel_db()
    context = prepare(order_id)
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(sql, (*parameters, order_id))
    finally:
        conn.close()
    before = dump_database()
    assert service.approve_cancel_confirmation(**context, now=NOW)["error"] == error
    assert dump_database() == before


def test_unpaid_becoming_paid_requires_new_confirmation(cancel_db):
    order_id = cancel_db(status="unpaid")
    context = prepare(order_id)
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE orders SET status='paid',paid_amount_cents=19900,paid_at=? WHERE order_id=?",
                ((NOW - timedelta(minutes=1)).isoformat(), order_id),
            )
    finally:
        conn.close()
    assert service.approve_cancel_confirmation(**context, now=NOW)["error"] == "CONFIRMATION_STALE"
    assert state(order_id)["refunds"] == []


@pytest.mark.parametrize(
    "table,event",
    [
        ("refund_requests", "INSERT"),
        ("operation_logs", "INSERT"),
        ("confirmations", "UPDATE"),
    ],
)
def test_failure_in_any_write_rolls_back_all_business_changes(cancel_db, table, event):
    order_id = cancel_db()
    context = prepare(order_id)
    conn = database.get_connection()
    try:
        conn.execute(
            f"CREATE TRIGGER cancel_failure BEFORE {event} ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'test cancellation failure'); END"
        )
    finally:
        conn.close()
    before = dump_database()
    with pytest.raises(sqlite3.IntegrityError, match="test cancellation failure"):
        service.approve_cancel_confirmation(**context, now=NOW)
    assert dump_database() == before


@pytest.mark.parametrize("same_confirmation", [True, False])
def test_concurrent_approval_never_duplicates_refund_or_audit(cancel_db, same_confirmation):
    order_id = cancel_db()
    first = prepare(order_id)
    second = first if same_confirmation else prepare(order_id, thread_id="other_thread")
    barrier = Barrier(2)

    def approve(context):
        barrier.wait(timeout=5)
        return service.approve_cancel_confirmation(**context, now=NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(approve, context) for context in (first, second)]
        results = [future.result(timeout=10) for future in futures]
    assert sum(result["ok"] for result in results) == (2 if same_confirmation else 1)
    if same_confirmation:
        assert results[0] == results[1]
    saved = state(order_id)
    assert saved["order"]["status"] == "canceled"
    assert len(saved["refunds"]) == len(saved["logs"]) == 1


def test_response_loss_after_commit_reuses_saved_result(cancel_db):
    order_id = cancel_db()
    context = prepare(order_id)
    committed = {}

    def lose_response():
        committed.update(service.approve_cancel_confirmation(**context, now=NOW))
        raise ConnectionError("test response lost after commit")

    with pytest.raises(ConnectionError):
        lose_response()
    before = dump_database()
    assert service.approve_cancel_confirmation(**context, now=NOW) == committed
    assert dump_database() == before


@pytest.mark.parametrize("recompute_hash", [False, True])
def test_changed_saved_parameters_are_rejected(cancel_db, recompute_hash):
    order_id = cancel_db()
    context = prepare(order_id)
    if recompute_hash:
        assert service.approve_cancel_confirmation(**context, now=NOW)["ok"]
    conn = database.get_connection()
    try:
        record = conn.execute(
            "SELECT * FROM confirmations WHERE confirmation_id=?", (context["confirmation_id"],)
        ).fetchone()
        params = json.loads(record["params_json"])
        params["reason"] = "不同请求"
        raw = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = (
            hashlib.sha256(raw.encode()).hexdigest() if recompute_hash else record["params_fingerprint"]
        )
        with conn:
            conn.execute(
                "UPDATE confirmations SET params_json=?,params_fingerprint=? WHERE confirmation_id=?",
                (raw, fingerprint, context["confirmation_id"]),
            )
    finally:
        conn.close()
    before = dump_database()
    error = "IDEMPOTENCY_CONFLICT" if recompute_hash else "CONFIRMATION_INVALID"
    assert service.approve_cancel_confirmation(**context, now=NOW)["error"] == error
    assert dump_database() == before


@pytest.mark.parametrize("sql", ["UPDATE operation_logs SET reason='changed'", "DELETE FROM operation_logs"])
def test_operation_logs_are_append_only(cancel_db, sql):
    order_id = cancel_db()
    assert service.approve_cancel_confirmation(**prepare(order_id), now=NOW)["ok"]
    before = dump_database()
    conn = database.get_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError), conn:
            conn.execute(sql)
    finally:
        conn.close()
    assert dump_database() == before


def test_reinitialization_and_demo_seed_preserve_existing_records(cancel_db):
    seed.seed_cancellation_orders(now=NOW)
    context = prepare("ORD-3001")
    assert service.approve_cancel_confirmation(**context, now=NOW)["ok"]
    before = dump_database()
    database.init_db()
    seed.seed_cancellation_orders(now=NOW + timedelta(days=2))
    assert dump_database() == before
    assert state("ORD-3002")["order"]["paid_at"] == (NOW - timedelta(hours=1)).isoformat()


def test_conflicting_legacy_cancellation_refunds_fail_migration_without_deletion(cancel_db):
    order_id = cancel_db()
    conn = database.get_connection()
    try:
        with conn:
            conn.execute("DROP INDEX ux_refunds_cancellation_order")
            conn.executemany(
                """
                INSERT INTO refund_requests (
                    refund_id,order_id,user_id,kind,amount_cents,reason,status,created_at,updated_at
                ) VALUES (?,?,'USER-001','cancellation',19900,'legacy','completed',?,?)
                """,
                [(rid, order_id, NOW.isoformat(), NOW.isoformat()) for rid in ("old1", "old2")],
            )
    finally:
        conn.close()
    before = dump_database()
    with pytest.raises(sqlite3.IntegrityError):
        database.init_db()
    assert dump_database() == before


def test_cancel_tool_requires_trusted_session_context(cancel_db):
    order_id = cancel_db()
    call = ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[
            {
                "id": "cancel",
                "type": "function",
                "function": {
                    "name": "cancel_order",
                    "arguments": json.dumps({"order_id": order_id, "reason": "不要了"}),
                },
            }
        ],
    ).tool_calls[0]
    before = dump_database()
    assert execute_tool(call, "USER-001", now=NOW)["error"] == "MISSING_CONTEXT"
    assert dump_database() == before


@pytest.mark.parametrize(
    "arguments",
    [
        "{}",
        "not json",
        '{"order_id":"x"}',
        '{"order_id":"x","reason":" "}',
        '{"order_id":"x","reason":"a","user_id":"USER-OTHER"}',
        '{"order_id":"x","reason":"a","amount_cents":100}',
        '{"order_id":"x","reason":"a","confirmation_id":"cfm_forged"}',
    ],
)
def test_cancel_tool_rejects_missing_or_forged_parameters(cancel_db, arguments):
    call = ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[
            {
                "id": "cancel",
                "type": "function",
                "function": {"name": "cancel_order", "arguments": arguments},
            }
        ],
    ).tool_calls[0]
    before = dump_database()
    assert (
        execute_tool(call, "USER-001", session_id="s", thread_id="t", now=NOW)["error"] == "INVALID_ARGUMENTS"
    )
    assert dump_database() == before


def install_intent(monkeypatch, intent, evidence):
    message = ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[
            {
                "id": "intent",
                "type": "function",
                "function": {
                    "name": "report_confirmation_intent",
                    "arguments": json.dumps(
                        {
                            "intent": intent,
                            "evidence": evidence,
                        }
                    ),
                },
            }
        ],
    )
    monkeypatch.setattr("app.agent.confirmation_intent.request_model", lambda *args: message)


@pytest.mark.parametrize("status", ["unpaid", "paid"])
def test_chat_prepares_then_approves_cancel_without_auto_execution(cancel_db, monkeypatch, status):
    order_id = cancel_db(status=status)
    call = ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[
            {
                "id": "cancel",
                "type": "function",
                "function": {
                    "name": "cancel_order",
                    "arguments": json.dumps({"order_id": order_id, "reason": "不要了"}),
                },
            }
        ],
    )
    monkeypatch.setattr("app.harness.runtime.request_model", lambda *args: call)
    messages = create_messages("离线测试")
    context = {"user_id": "USER-001", "session_id": "chat_cancel", "thread_id": "chat_cancel"}
    answer = run_agent("取消订单，不要了", messages=messages, client=None, now=NOW, **context)
    assert "确认取消" in answer
    assert state(order_id)["order"]["status"] == status
    assert state(order_id)["refunds"] == []
    install_intent(monkeypatch, "approve", "确认取消")

    answer = run_agent("确认取消", messages=messages, client=None, now=NOW, **context)

    assert "已取消" in answer
    assert state(order_id)["order"]["status"] == "canceled"
    if status == "paid":
        assert state(order_id)["refunds"][0]["refund_id"] in answer
        assert "模拟" in answer


@pytest.mark.parametrize(
    "intent,message,status",
    [
        ("reject", "算了不取消了", "rejected"),
        ("other", "取消后会怎样", "pending"),
    ],
)
def test_chat_reject_or_question_does_not_cancel(cancel_db, monkeypatch, intent, message, status):
    order_id = cancel_db()
    context = prepare(order_id)
    install_intent(monkeypatch, intent, message)
    run_agent(
        message,
        user_id=context["user_id"],
        messages=create_messages("test"),
        client=None,
        session_id=context["session_id"],
        thread_id=context["thread_id"],
        now=NOW,
    )
    saved = state(order_id)
    assert saved["order"]["status"] == "paid"
    assert saved["confirmations"][0]["status"] == status
    assert saved["refunds"] == saved["logs"] == []


def test_restart_menu_can_resume_cancel_and_does_not_replay_completed_action(cancel_db, monkeypatch, capsys):
    order_id = cancel_db()
    context = prepare(order_id)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(demo_chat, "datetime", FixedDatetime)
    monkeypatch.setattr("app.harness.runtime.datetime", FixedDatetime)
    monkeypatch.setattr(demo_chat, "create_client", lambda: None)
    install_intent(monkeypatch, "approve", "确认取消")
    questions = iter(["1", "确认取消", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(questions))
    demo_chat.main([])
    assert "待确认取消" in capsys.readouterr().out
    assert state(order_id)["order"]["status"] == "canceled"
    before = dump_database()
    questions = iter(["1", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(questions))
    demo_chat.main([])
    assert dump_database() == before
    assert context["confirmation_id"] == state(order_id)["logs"][0]["confirmation_id"]


def test_cli_new_cancel_request_approval_and_snapshot_are_connected(cancel_db, monkeypatch, capsys):
    order_id = cancel_db()

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    request = ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[
            {
                "id": "cancel_cli",
                "type": "function",
                "function": {
                    "name": "cancel_order",
                    "arguments": json.dumps({"order_id": order_id, "reason": "不要了"}),
                },
            }
        ],
    )

    def model(client, messages, tools):
        assert any(tool["function"]["name"] == "cancel_order" for tool in tools)
        return request

    monkeypatch.setattr(demo_chat, "datetime", FixedDatetime)
    monkeypatch.setattr("app.harness.runtime.datetime", FixedDatetime)
    monkeypatch.setattr("app.harness.runtime.request_model", model)
    monkeypatch.setattr(demo_chat, "create_client", lambda: None)
    install_intent(monkeypatch, "approve", "确认取消")
    questions = iter([f"取消 {order_id}，不要了", "确认取消", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(questions))

    demo_chat.main([])

    saved = state(order_id)
    assert saved["order"]["status"] == "canceled"
    assert len(saved["refunds"]) == len(saved["logs"]) == 1
    assert saved["refunds"][0]["refund_id"] in capsys.readouterr().out
    conn = database.get_connection()
    try:
        row = conn.execute("SELECT version,messages_json FROM chat_threads").fetchone()
        assert row["version"] == 2
        messages = json.loads(row["messages_json"])
        tool_result = json.loads(next(m["content"] for m in messages if m["role"] == "tool"))
        assert tool_result["tool"] == "cancel_order"
        assert tool_result["status"] == "awaiting_confirmation"
    finally:
        conn.close()
