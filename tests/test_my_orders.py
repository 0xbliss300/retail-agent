"""本人订单列表、分页、详情衔接和只读聊天入口。"""

import json

import pytest
from openai.types.chat import ChatCompletionMessage

from app.business import repository, service
from app.harness.tool_executor import execute_tool
from app.storage import database
from scripts import demo_chat


def dump_database():
    conn = database.get_connection()
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def model_call(name, arguments):
    return ChatCompletionMessage(
        role="assistant", content=None,
        tool_calls=[{
            "id": f"call_{name}", "type": "function",
            "function": {"name": name, "arguments": arguments},
        }],
    )


@pytest.fixture
def query_orders(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()
    conn = database.get_connection()
    try:
        with conn:
            # 故意按乱序插入，确认分页使用稳定的订单号排序。
            conn.executemany(
                """
                INSERT INTO orders (order_id,user_id,status,paid_amount_cents,paid_at,shipped_at)
                VALUES (?,?,?,?,?,?)
                """,
                [
                    ("ORD-C", "USER-001", "unpaid", 0, None, None),
                    ("ORD-A", "USER-001", "shipped", 12900,
                     "2026-10-25T02:00:00+00:00", "2026-10-26T02:00:00+00:00"),
                    ("ORD-B", "USER-001", "canceled", 8900, "2026-10-25T02:00:00+00:00", None),
                    ("ORD-PRIVATE", "USER-002", "paid", 99900, None, None),
                ],
            )
    finally:
        conn.close()

    def query(arguments="{}", *, user_id="USER-001", name="list_orders"):
        before = dump_database()
        result = execute_tool(model_call(name, arguments).tool_calls[0], user_id)
        assert dump_database() == before
        return result

    return query


def test_list_returns_only_owned_order_summaries(query_orders):
    result = query_orders()

    assert result["ok"] is True
    assert [row["order_id"] for row in result["orders"]] == ["ORD-1001", "ORD-A", "ORD-B", "ORD-C"]
    assert result["orders"][1] == {
        "order_id": "ORD-A", "status": "shipped", "paid_amount_cents": 12900,
        "paid_at": "2026-10-25T02:00:00+00:00", "shipped_at": "2026-10-26T02:00:00+00:00",
        "delivered_at": None,
    }
    assert all("user_id" not in row for row in result["orders"])
    assert result["limit"] == 10
    assert result["offset"] == 0
    assert result["has_more"] is False
    assert result["next_offset"] is None


def test_pagination_is_stable_and_has_no_duplicates(query_orders):
    first = query_orders('{"limit":2,"offset":0}')
    second = query_orders('{"limit":2,"offset":2}')

    assert [row["order_id"] for row in first["orders"]] == ["ORD-1001", "ORD-A"]
    assert first["has_more"] is True
    assert first["next_offset"] == 2
    assert [row["order_id"] for row in second["orders"]] == ["ORD-B", "ORD-C"]
    assert second["has_more"] is False
    assert second["next_offset"] is None


@pytest.mark.parametrize("user_id,arguments", [
    ("USER-EMPTY", "{}"), ("USER-001", '{"offset":100}'),
])
def test_empty_page_is_a_successful_query(query_orders, user_id, arguments):
    result = query_orders(arguments, user_id=user_id)
    assert result["ok"] is True
    assert result["orders"] == []
    assert result["has_more"] is False
    assert result["next_offset"] is None


def test_identity_is_taken_from_executor_context(query_orders):
    result = query_orders(user_id="USER-002")
    assert result["ok"] is True
    assert [row["order_id"] for row in result["orders"]] == ["ORD-1002", "ORD-PRIVATE"]


def test_repository_filters_orders_before_applying_page(query_orders):
    result = repository.get_orders_for_user(user_id="USER-002", limit=1, offset=1)
    assert [row["order_id"] for row in result] == ["ORD-PRIVATE"]


@pytest.mark.parametrize("arguments", [
    "not json", "[]", "null", '{"user_id":"USER-002"}', '{"order_id":"ORD-PRIVATE"}',
    '{"limit":0}', '{"limit":-1}', '{"limit":51}', '{"limit":true}', '{"limit":"2"}',
    '{"limit":1.5}', '{"offset":-1}', '{"offset":true}', '{"offset":"1"}',
    '{"offset":9223372036854775808}',
])
def test_invalid_pagination_and_identity_spoofing_are_rejected(query_orders, arguments):
    assert query_orders(arguments) == {"ok": False, "error": "INVALID_ARGUMENTS"}


@pytest.mark.parametrize("limit,offset", [(0, 0), (51, 0), (10, -1), (True, 0)])
def test_service_also_enforces_pagination_bounds(query_orders, limit, offset):
    assert service.list_orders(user_id="USER-001", limit=limit, offset=offset) == {
        "ok": False, "error": "INVALID_ARGUMENTS",
    }


@pytest.mark.parametrize("user_id", [None, "", "  ", 42])
def test_list_rejects_missing_trusted_identity(query_orders, user_id):
    assert query_orders(user_id=user_id) == {"ok": False, "error": "MISSING_CONTEXT"}


def test_large_list_is_bounded_instead_of_returning_all_rows(query_orders):
    conn = database.get_connection()
    try:
        with conn:
            conn.executemany(
                "INSERT INTO orders (order_id,user_id,status,paid_amount_cents) "
                "VALUES (?,'USER-001','paid',100)",
                [(f"PAGE-{i:03d}",) for i in range(60)],
            )
    finally:
        conn.close()

    default = query_orders()
    largest = query_orders('{"limit":50}')
    assert len(default["orders"]) == 10
    assert default["has_more"] is True
    assert default["next_offset"] == 10
    assert len(largest["orders"]) == 50
    assert largest["has_more"] is True
    assert largest["next_offset"] == 50


def test_existing_detail_tool_can_query_an_order_from_the_list(query_orders):
    result = query_orders('{"order_id":"ORD-A"}', name="get_order")
    assert result["ok"] is True
    assert result["order"]["order_id"] == "ORD-A"
    assert result["order"]["paid_amount_cents"] == 12900
    assert query_orders('{"order_id":"ORD-PRIVATE"}', name="get_order") == {
        "ok": False, "error": "ORDER_NOT_FOUND",
    }


def test_chat_lists_then_queries_selected_order_and_saves_history(query_orders, monkeypatch, capsys):
    before = dump_database()
    responses = iter([
        model_call("list_orders", "{}"),
        ChatCompletionMessage(role="assistant", content="本人订单有 ORD-1001、ORD-A、ORD-B、ORD-C。"),
        model_call("get_order", '{"order_id":"ORD-A"}'),
        ChatCompletionMessage(role="assistant", content="ORD-A 已发货，实付129元。"),
    ])

    def model(client, messages, tools):
        assert any(tool["function"]["name"] == "list_orders" for tool in tools)
        return next(responses)

    monkeypatch.setattr("app.harness.runtime.request_model", model)
    monkeypatch.setattr(demo_chat, "create_client", lambda: None)
    questions = iter(["查询我的订单", "查看刚才第2个订单详情", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(questions))

    demo_chat.main([])

    output = capsys.readouterr().out
    assert "ORD-A" in output
    assert "129元" in output
    assert "ORD-PRIVATE" not in output
    conn = database.get_connection()
    try:
        messages = json.loads(conn.execute("SELECT messages_json FROM chat_threads").fetchone()[0])
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        assert [row["order_id"] for row in results[0]["orders"]] == [
            "ORD-1001", "ORD-A", "ORD-B", "ORD-C",
        ]
        assert results[1]["order"]["order_id"] == "ORD-A"
        after = list(conn.iterdump())
        # 只排除正常聊天产生的快照行，其余结构和业务数据必须保持不变。
        def without_history_rows(lines):
            return [line for line in lines if not line.startswith('INSERT INTO "chat_threads"')]
        assert without_history_rows(after) == without_history_rows(before)
    finally:
        conn.close()
