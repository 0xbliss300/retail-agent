"""退款进度查询：真实 SQLite 与工具执行链路，模型使用离线替身。"""

import json

import pytest
from openai.types.chat import ChatCompletionMessage

from app.harness.runtime import create_messages, run_agent
from app.harness.tool_executor import execute_tool
from app.storage import database


def model_reply(*, arguments=None, content=None):
    calls = None
    if arguments is not None:
        calls = [{
            "id": "call_refund_status",
            "type": "function",
            "function": {"name": "get_refund_status", "arguments": arguments},
        }]
    return ChatCompletionMessage(role="assistant", content=content, tool_calls=calls)


def dump_database():
    conn = database.get_connection()
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def add_refund(
    refund_id, *, order_id="ORD-QUERY", user_id="USER-QUERY",
    status="pending", kind="after_sales", created_at="2026-10-30T02:00:00+00:00",
):
    """仅供测试准备模拟状态，不代表已实现审核或结算入口。"""
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO refund_requests (
                    refund_id, order_id, user_id, kind,
                    amount_cents, reason, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (refund_id, order_id, user_id, kind, 8900, "商品破损",
                 status, created_at, created_at),
            )
    finally:
        conn.close()


@pytest.fixture
def query_refund(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()
    conn = database.get_connection()
    try:
        with conn:
            conn.executemany(
                """
                INSERT INTO orders (order_id, user_id, status, paid_amount_cents)
                VALUES (?, ?, 'shipped', 8900)
                """,
                [
                    ("ORD-QUERY", "USER-QUERY"),
                    ("ORD-EMPTY", "USER-QUERY"),
                    ("ORD-OTHER", "USER-OTHER"),
                ],
            )
    finally:
        conn.close()

    def invoke(arguments='{"order_id":"ORD-QUERY"}', *, user_id="USER-QUERY"):
        before = dump_database()
        call = model_reply(arguments=arguments).tool_calls[0]
        result = execute_tool(call, user_id)
        assert dump_database() == before
        return result

    return invoke


@pytest.mark.parametrize("status", ["pending", "approved", "rejected", "completed", "failed"])
def test_query_reports_saved_application_status(query_refund, status):
    add_refund("RF-QUERY", status=status)

    result = query_refund()

    assert result == {
        "ok": True,
        "order_id": "ORD-QUERY",
        "refund": {
            "refund_id": "RF-QUERY",
            "amount_cents": 8900,
            "reason": "商品破损",
            "status": status,
            "created_at": "2026-10-30T02:00:00+00:00",
            "updated_at": "2026-10-30T02:00:00+00:00",
        },
    }


def test_query_selects_latest_after_sales_instead_of_insertion_order(query_refund):
    add_refund("RF-NEW", status="completed")
    # 后插入的是更早的记录，不能按 rowid 单独选择最新申请。
    add_refund("RF-OLD", status="rejected", created_at="2026-10-28T02:00:00+00:00")
    add_refund("RF-CANCEL", kind="cancellation", status="completed",
               created_at="2026-10-31T02:00:00+00:00")

    result = query_refund()

    assert result["ok"] is True
    assert result["refund"]["refund_id"] == "RF-NEW"
    assert result["refund"]["status"] == "completed"


def test_query_breaks_equal_creation_time_by_insertion_order(query_refund):
    add_refund("RF-FIRST", status="rejected")
    add_refund("RF-SECOND", status="completed")

    result = query_refund()

    assert result["ok"] is True
    assert result["refund"]["refund_id"] == "RF-SECOND"


def test_owned_order_without_application_returns_none(query_refund):
    result = query_refund('{"order_id":"ORD-EMPTY"}')
    assert result == {"ok": True, "order_id": "ORD-EMPTY", "refund": None}


@pytest.mark.parametrize(
    "arguments,user_id",
    [
        ('{"order_id":"ORD-OTHER"}', "USER-QUERY"),
        ('{"order_id":"ORD-MISSING"}', "USER-QUERY"),
        ('{"order_id":"ORD-QUERY"}', "USER-OTHER"),
    ],
)
def test_unavailable_orders_do_not_disclose_refunds(query_refund, arguments, user_id):
    add_refund("RF-PRIVATE", order_id="ORD-OTHER", user_id="USER-OTHER")
    add_refund("RF-QUERY")

    result = query_refund(arguments, user_id=user_id)

    assert result == {"ok": False, "error": "ORDER_NOT_FOUND"}


@pytest.mark.parametrize("arguments", [
    "not json", "[]", "{}", '{"order_id":42}', '{"order_id":"   "}',
    '{"order_id":"ORD-QUERY","user_id":"USER-OTHER"}',
    '{"order_id":"ORD-QUERY","refund_id":"RF-OTHER"}',
])
def test_invalid_or_spoofed_arguments_are_rejected(query_refund, arguments):
    assert query_refund(arguments) == {"ok": False, "error": "INVALID_ARGUMENTS"}


def test_runtime_executes_refund_query_and_keeps_tool_result(query_refund, monkeypatch):
    add_refund("RF-CHAT")
    before = dump_database()
    responses = iter([
        model_reply(arguments='{"order_id":"ORD-QUERY"}'),
        model_reply(content="申请 RF-CHAT 待处理，金额89元。"),
    ])

    def offline_model(client, messages, tools):
        assert any(tool["function"]["name"] == "get_refund_status" for tool in tools)
        return next(responses)

    monkeypatch.setattr("app.harness.runtime.request_model", offline_model)
    messages = create_messages("离线测试")

    answer = run_agent("ORD-QUERY 的退款到哪一步了", "USER-QUERY", messages, None)

    payload = json.loads(messages[-2]["content"])
    assert payload["ok"] is True
    assert payload["refund"]["refund_id"] == "RF-CHAT"
    assert payload["refund"]["status"] == "pending"
    assert messages[-2]["tool_call_id"] == "call_refund_status"
    assert "RF-CHAT" in answer
    assert dump_database() == before
