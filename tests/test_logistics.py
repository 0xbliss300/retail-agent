"""物流迁移、模拟数据和只读聊天查询的完整链路。"""

import json
import sqlite3

import pytest
from openai.types.chat import ChatCompletionMessage

from app.business import repository, service
from app.harness.tool_executor import execute_tool
from app.storage import database, seed
from scripts import demo_chat


def dump_database():
    conn = database.get_connection()
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def business_state():
    conn = database.get_connection()
    try:
        return {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("orders", "refund_requests", "confirmations", "chat_threads")
        }
    finally:
        conn.close()


def logistics_call(arguments):
    return ChatCompletionMessage(
        role="assistant", content=None,
        tool_calls=[{
            "id": "call_logistics", "type": "function",
            "function": {"name": "get_logistics", "arguments": arguments},
        }],
    )


def test_migration_preserves_legacy_order(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    conn = sqlite3.connect(path)
    try:
        with conn:
            conn.execute(
                "CREATE TABLE orders (order_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
                "status TEXT NOT NULL, paid_amount_cents INTEGER NOT NULL)"
            )
            conn.execute("INSERT INTO orders VALUES ('LEGACY','USER-001','shipped',1234)")
    finally:
        conn.close()

    database.init_db()
    database.init_db()

    conn = database.get_connection()
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"shipments", "logistics_events"} <= tables
        row = dict(conn.execute("SELECT * FROM orders WHERE order_id='LEGACY'").fetchone())
        assert row == {
            "order_id": "LEGACY", "user_id": "USER-001", "status": "shipped",
            "paid_amount_cents": 1234, "paid_at": None, "shipped_at": None, "delivered_at": None,
        }
        assert conn.execute("SELECT COUNT(*) FROM shipments").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


@pytest.fixture
def logistics_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()
    conn = database.get_connection()
    try:
        with conn:
            conn.executemany(
                "INSERT INTO orders (order_id,user_id,status,paid_amount_cents) VALUES (?,?,?,8900)",
                [
                    ("ORD-LOG", "USER-001", "shipped"),
                    ("ORD-NOLOG", "USER-001", "paid"),
                    ("ORD-EMPTYTRACK", "USER-001", "shipped"),
                    ("ORD-PRIVATE", "USER-002", "shipped"),
                ],
            )
            conn.executemany(
                "INSERT INTO shipments (order_id,user_id,carrier,tracking_number) VALUES (?,?,?,?)",
                [
                    ("ORD-LOG", "USER-001", "模拟承运商", "MOCK-LOG"),
                    ("ORD-EMPTYTRACK", "USER-001", "模拟承运商", "MOCK-EMPTY"),
                    ("ORD-PRIVATE", "USER-002", "私人承运商", "PRIVATE-TRACK"),
                ],
            )
            # 故意倒序插入，以检查查询是否真正按轨迹时间排序。
            conn.executemany(
                "INSERT INTO logistics_events (event_id,order_id,user_id,occurred_at,description) "
                "VALUES (?,?,?,?,?)",
                [
                    ("event_later", "ORD-LOG", "USER-001", "2026-10-31T03:00:00+00:00", "到达分拨中心"),
                    ("event_earlier", "ORD-LOG", "USER-001", "2026-10-30T03:00:00+00:00", "已揽收"),
                    ("event_private", "ORD-PRIVATE", "USER-002", "2026-10-31T03:00:00+00:00", "私人轨迹"),
                ],
            )
    finally:
        conn.close()


@pytest.fixture
def query_logistics(logistics_db):
    def query(arguments='{"order_id":"ORD-LOG"}', *, user_id="USER-001"):
        before = dump_database()
        result = execute_tool(logistics_call(arguments).tool_calls[0], user_id)
        assert dump_database() == before
        return result
    return query


def test_logistics_returns_owned_shipment_and_chronological_events(query_logistics):
    assert query_logistics() == {
        "ok": True,
        "order_id": "ORD-LOG",
        "logistics": {
            "carrier": "模拟承运商", "tracking_number": "MOCK-LOG",
            "events": [
                {"occurred_at": "2026-10-30T03:00:00+00:00", "description": "已揽收"},
                {"occurred_at": "2026-10-31T03:00:00+00:00", "description": "到达分拨中心"},
            ],
        },
    }


def test_owned_order_without_logistics_is_not_reported_as_missing(query_logistics):
    assert query_logistics('{"order_id":"ORD-NOLOG"}') == {
        "ok": True, "order_id": "ORD-NOLOG", "logistics": None,
    }


def test_shipment_without_events_returns_empty_track(query_logistics):
    result = query_logistics('{"order_id":"ORD-EMPTYTRACK"}')
    assert result["ok"] is True
    assert result["logistics"]["tracking_number"] == "MOCK-EMPTY"
    assert result["logistics"]["events"] == []


@pytest.mark.parametrize("order_id,user_id", [
    ("ORD-PRIVATE", "USER-001"),
    ("ORD-MISSING", "USER-001"),
    ("ORD-LOG' OR 1=1 --", "USER-001"),
    ("ORD-LOG", "USER-002"),
])
def test_inaccessible_orders_have_identical_errors(query_logistics, order_id, user_id):
    assert query_logistics(json.dumps({"order_id": order_id}), user_id=user_id) == {
        "ok": False, "error": "ORDER_NOT_FOUND",
    }


def test_repository_also_filters_owner(logistics_db):
    assert repository.get_logistics_for_user(user_id="USER-001", order_id="ORD-PRIVATE") is None


@pytest.mark.parametrize("arguments", [
    "not json", "[]", "{}", '{"order_id":null}', '{"order_id":42}', '{"order_id":"  "}',
    '{"order_id":"ORD-LOG","user_id":"USER-002"}',
    '{"order_id":"ORD-LOG","tracking_number":"PRIVATE-TRACK"}',
])
def test_invalid_or_forged_tool_arguments_are_rejected(query_logistics, arguments):
    assert query_logistics(arguments) == {"ok": False, "error": "INVALID_ARGUMENTS"}


@pytest.mark.parametrize("sql", [
    "INSERT INTO shipments VALUES ('ORD-NOLOG','USER-002','bad','bad')",
    "INSERT INTO logistics_events VALUES ('bad','ORD-NOLOG','USER-001','2026-11-01','bad')",
    "INSERT INTO logistics_events VALUES ('bad','ORD-LOG','USER-002','2026-11-01','bad')",
])
def test_constraints_reject_wrong_owner_or_orphan_events(logistics_db, sql):
    before = dump_database()
    conn = database.get_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError), conn:
            conn.execute(sql)
    finally:
        conn.close()
    assert dump_database() == before


def test_reinitialization_preserves_existing_business_and_logistics(logistics_db):
    before = business_state()
    original = dump_database()
    database.init_db()
    database.init_db()
    assert business_state() == before
    assert dump_database() == original


def test_logistics_seed_is_repeatable_and_does_not_reset_existing_tracks(logistics_db):
    seed.seed_orders()
    before = business_state()
    seed.seed_logistics()
    result = service.get_logistics(user_id="USER-001", order_id="ORD-2002")
    assert result["ok"] is True
    assert result["logistics"]["tracking_number"] == "MOCK2002001"
    assert len(result["logistics"]["events"]) == 3
    assert business_state() == before
    conn = database.get_connection()
    try:
        with conn:
            conn.execute("UPDATE shipments SET carrier='保留修改' WHERE order_id='ORD-2002'")
            conn.execute(
                "INSERT INTO logistics_events VALUES "
                "('manual','ORD-2002','USER-001','2026-11-01T03:00:00+00:00','保留新增轨迹')"
            )
    finally:
        conn.close()
    saved = dump_database()
    seed.seed_logistics()
    seed.seed_logistics()
    assert dump_database() == saved


def test_seed_does_not_attach_demo_logistics_to_another_users_order(logistics_db):
    conn = database.get_connection()
    try:
        with conn:
            conn.execute("INSERT INTO orders VALUES ('ORD-2002','OTHER','shipped',8900,NULL,NULL,NULL)")
    finally:
        conn.close()
    before = dump_database()
    seed.seed_logistics()
    assert dump_database() == before


def test_chat_entry_executes_logistics_query_and_saves_tool_result(logistics_db, monkeypatch, capsys):
    before = business_state()
    responses = iter([
        logistics_call('{"order_id":"ORD-LOG"}'),
        ChatCompletionMessage(role="assistant", content="模拟物流 MOCK-LOG：已揽收，后到达分拨中心。"),
    ])

    def model(client, messages, tools):
        assert any(tool["function"]["name"] == "get_logistics" for tool in tools)
        return next(responses)

    monkeypatch.setattr("app.harness.runtime.request_model", model)
    monkeypatch.setattr(demo_chat, "create_client", lambda: None)
    questions = iter(["ORD-LOG 的快递到哪了？", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(questions))

    demo_chat.main([])

    assert "MOCK-LOG" in capsys.readouterr().out
    conn = database.get_connection()
    try:
        rows = conn.execute("SELECT messages_json FROM chat_threads").fetchall()
        assert len(rows) == 1
        messages = json.loads(rows[0][0])
        payload = json.loads(next(message["content"] for message in messages if message["role"] == "tool"))
        assert payload["logistics"]["tracking_number"] == "MOCK-LOG"
        assert payload["logistics"]["events"][-1]["description"] == "到达分拨中心"
    finally:
        conn.close()
    # 聊天保存历史是预期行为；订单、退款和确认应保持不变。
    after = business_state()
    assert {k: v for k, v in after.items() if k != "chat_threads"} == {
        k: v for k, v in before.items() if k != "chat_threads"
    }
