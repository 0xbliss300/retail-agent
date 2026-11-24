"""HTTP API：健康检查、会话保存、待确认操作和错误路径。"""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from openai.types.chat import ChatCompletionMessage

from app import main
from app.auth import COOKIE_NAME
from app.storage import database


def text_reply(content):
    return ChatCompletionMessage(role="assistant", content=content)


def tool_reply(name, arguments):
    return ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[
            {
                "id": f"call_{name}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    )


@pytest.fixture
def api_client(tmp_path, monkeypatch, login_demo):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()

    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO orders (order_id, user_id, status, paid_amount_cents)
                VALUES ('ORD-API-1', 'USER-001', 'unpaid', 0)
                """
            )
    finally:
        conn.close()

    monkeypatch.setattr(main, "create_client", lambda: None)
    with TestClient(main.app) as client:
        login_demo(client)
        yield client


def chat(client, message, **context):
    return client.post("/chat", json={"message": message, **context})


def read_database(query, parameters=()):
    conn = database.get_connection()
    try:
        return [dict(row) for row in conn.execute(query, parameters)]
    finally:
        conn.close()


def test_health(api_client):
    response = api_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "ok",
        "version": main.APP_VERSION,
    }


def test_logged_in_accounts_only_get_their_orders_through_agent(api_client, login_demo, monkeypatch):
    def model(_client, messages, _tools):
        message = messages[-1]
        if message["role"] == "tool":
            return text_reply(message["content"])
        if message["content"] == "查询我的订单":
            return tool_reply("list_orders", {})
        return tool_reply("get_order", {"order_id": message["content"]})

    def stream_model(*args):
        yield "message", model(*args)

    monkeypatch.setattr("app.harness.runtime.request_model", model)
    monkeypatch.setattr("app.harness.runtime.stream_model", stream_model)
    with TestClient(main.app) as second:
        missing_login = chat(second, "查询我的订单")
        assert missing_login.status_code == 401
        assert second.get("/orders").status_code == 401
        assert second.post("/chat/stream", json={"message": "查询我的订单"}).status_code == 401
        bad_login = second.post("/auth/login", json={"email": "demo2@retailops.local", "password": "wrong"})
        assert bad_login.status_code == 401
        assert second.get("/auth/me").status_code == 401
        login_demo(second, second=True)

        assert api_client.get("/auth/me").json()["user_id"] == "USER-001"
        assert second.get("/auth/me").json()["user_id"] == "USER-002"
        assert second.get("/orders", headers={"X-RetailOps-Account": "USER-001"}).status_code == 401
        for client, own, other in (
            (api_client, "ORD-1001", "ORD-1002"),
            (second, "ORD-1002", "ORD-1001"),
        ):
            response = chat(client, "查询我的订单")
            assert response.status_code == 200
            orders = json.loads(response.json()["reply"])["orders"]
            order_ids = {order["order_id"] for order in orders}
            assert own in order_ids and other not in order_ids
            stream = client.post("/chat/stream", json={"message": "查询我的订单"})
            assert stream.status_code == 200
            assert "event: done" in stream.text
            assert own in stream.text and other not in stream.text
            assert json.loads(chat(client, own).json()["reply"])["order"]["order_id"] == own
            unavailable = json.loads(chat(client, other).json()["reply"])
            assert unavailable["error"] == "ORDER_NOT_FOUND"
            assert "order" not in unavailable
            assert client.get(f"/orders/{other}").status_code == 404

        first_history = chat(api_client, "查询我的订单").json()
        assert chat(
            second, "查询我的订单",
            session_id=first_history["session_id"], thread_id=first_history["thread_id"],
        ).status_code == 404
        previous_cookie = second.cookies.get(COOKIE_NAME)
        assert second.post("/auth/logout").status_code == 200
        second.cookies.set(COOKIE_NAME, previous_cookie)
        assert chat(second, "查询我的订单").status_code == 401


def test_chat_starts_and_continues_a_conversation(api_client, monkeypatch):
    monkeypatch.setattr(
        "app.harness.runtime.request_model",
        lambda *args: text_reply("你好。"),
    )

    first = chat(api_client, "你好")
    assert first.status_code == 200
    body = first.json()
    assert body["reply"] == "你好。"
    assert body["history_saved"] is True
    assert body["warning"] is None
    assert body["pending_action"] is None
    assert body["session_id"].startswith("sess_")
    assert body["thread_id"].startswith("thread_")

    session_id = body["session_id"]
    thread_id = body["thread_id"]
    second = chat(api_client, "继续", session_id=session_id, thread_id=thread_id)
    assert second.status_code == 200
    assert second.json()["session_id"] == session_id
    assert second.json()["thread_id"] == thread_id

    rows = read_database("SELECT * FROM chat_threads")
    assert len(rows) == 1
    assert rows[0]["title"] == "你好"
    assert rows[0]["version"] == 2
    messages = json.loads(rows[0]["messages_json"])
    assert [message["role"] for message in messages] == [
        "system", "user", "assistant", "user", "assistant",
    ]


@pytest.mark.parametrize("opening,use_model", [
    ("帮我取消那笔 89 元的订单。", False),
    ("帮我取消那笔 89 元的订单，原因是买重复了。", True),
])
def test_cancel_by_amount_cannot_prepare_until_user_selects_a_candidate(
    api_client, monkeypatch, opening, use_model,
):
    with database.get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO orders (order_id, user_id, status, paid_amount_cents, paid_at)
            VALUES (?, ?, 'paid', 8900, ?)
            """,
            [(f"ORD-API-89{i}", owner, datetime.now(UTC).isoformat())
             for i, owner in [(1, "USER-001"), (2, "USER-001"), (3, "USER-002")]],
        )
    conn.close()

    lookup = tool_reply("find_orders_by_amount", {
        "paid_amount_cents": 8900, "purpose": "cancel_order", "reason": "买重复了",
    })
    guess = tool_reply("cancel_order", {"order_id": "ORD-API-891", "reason": "未说明"})
    message = lookup.model_copy(update={"tool_calls": [*guess.tool_calls, *lookup.tool_calls]})
    replies = iter([guess, message])

    def stream_model(*_):
        assert use_model
        yield "token", "请先提供取消原因。"
        yield "message", next(replies)

    monkeypatch.setattr("app.harness.runtime.stream_model", stream_model)

    def send(text, **context):
        response = chat(api_client, text, **context)
        assert response.status_code == 200
        return response.json()

    streamed = api_client.post("/chat/stream", json={"message": opening})
    assert streamed.status_code == 200
    assert "请先提供取消原因" not in streamed.text
    done = next(block for block in streamed.text.split("\n\n") if block.startswith("event: done\n"))
    choices = json.loads(done.split("\ndata: ", maxsplit=1)[1])
    context = {key: choices[key] for key in ("session_id", "thread_id")}
    assert "ORD-API-891" in choices["reply"] and "ORD-API-892" in choices["reply"]
    assert "付款时间" in choices["reply"] and "ORD-API-893" not in choices["reply"]
    assert choices["pending_action"] is None
    snapshot_sql = """
        SELECT messages_json, cancel_selection_json FROM chat_threads
        WHERE session_id = ? AND thread_id = ?
    """
    snapshot_key = (context["session_id"], context["thread_id"])
    saved_selection = read_database(snapshot_sql, snapshot_key)[0]["cancel_selection_json"]
    assert [order["order_id"] for order in json.loads(saved_selection)["orders"]] == [
        "ORD-API-891", "ORD-API-892",
    ]
    assert send("确认取消", **context)["pending_action"] is None
    assert send("ORD-API-893", **context)["pending_action"] is None

    policy = send("先告诉我取消政策", **context)
    assert policy["pending_action"] is None
    assert "付款后24小时内可以取消" in policy["reply"]
    assert "ORD-API-891" not in policy["reply"]
    snapshot = read_database(snapshot_sql, snapshot_key)[0]
    assert snapshot["cancel_selection_json"] == saved_selection
    assert json.loads(snapshot["messages_json"])[-1] == {"role": "assistant", "content": policy["reply"]}
    assert read_database("SELECT * FROM confirmations") == []
    assert read_database("SELECT * FROM refund_requests") == []

    card = send("选第二笔", **context)["pending_action"]
    assert card["order_id"] == "ORD-API-892"
    assert read_database(snapshot_sql, snapshot_key)[0]["cancel_selection_json"] is None
    assert "89.00元" in card["summary"]
    assert ("买重复了" if use_model else "未说明") in card["summary"]
    assert read_database(
        "SELECT status FROM orders WHERE order_id IN ('ORD-API-891', 'ORD-API-892') ORDER BY order_id",
    ) == [{"status": "paid"}, {"status": "paid"}]
    assert read_database("SELECT * FROM refund_requests") == []

    payload = {**context, "confirmation_id": card["confirmation_id"], "decision": "approve"}
    first = api_client.post("/confirm", json=payload)
    second = api_client.post("/confirm", json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json()["reply"] == second.json()["reply"]
    assert read_database(
        "SELECT status FROM orders WHERE order_id IN ('ORD-API-891', 'ORD-API-892') ORDER BY order_id",
    ) == [{"status": "paid"}, {"status": "canceled"}]
    assert read_database("SELECT order_id, amount_cents FROM refund_requests") == [
        {"order_id": "ORD-API-892", "amount_cents": 8900},
    ]
    assert read_database("SELECT order_id FROM operation_logs") == [{"order_id": "ORD-API-892"}]


@pytest.mark.parametrize(
    "payload,status,error",
    [
        ({"message": "   "}, 422, "EMPTY_MESSAGE"),
        (
            {"message": "查询订单", "session_id": "only_session"},
            400,
            "INVALID_SESSION_CONTEXT",
        ),
        (
            {"message": "查询订单", "session_id": "missing", "thread_id": "missing"},
            404,
            "CONVERSATION_NOT_FOUND",
        ),
        ({"message": "查询订单", "user_id": "USER-002"}, 422, None),
    ],
)
def test_chat_rejects_invalid_requests(api_client, payload, status, error):
    response = api_client.post("/chat", json=payload)

    assert response.status_code == status
    if error is not None:
        assert response.json()["detail"]["error"] == error


def test_chat_prepares_then_confirms_a_cancel(api_client, monkeypatch):
    monkeypatch.setattr(
        "app.harness.runtime.request_model",
        lambda *args: tool_reply(
            "cancel_order",
            {"order_id": "ORD-API-1", "reason": "不需要了"},
        ),
    )

    first = chat(api_client, "取消 ORD-API-1，不需要了")
    assert first.status_code == 200
    body = first.json()
    assert body["pending_action"]["tool"] == "cancel_order"
    assert body["pending_action"]["status"] == "pending"
    assert "确认取消" in body["reply"]

    session_id = body["session_id"]
    thread_id = body["thread_id"]
    assert read_database(
        "SELECT status FROM orders WHERE order_id = 'ORD-API-1'"
    )[0]["status"] == "unpaid"
    assert read_database("SELECT * FROM operation_logs") == []
    assert read_database("SELECT * FROM refund_requests") == []

    intent = ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[
            {
                "id": "intent",
                "type": "function",
                "function": {
                    "name": "report_confirmation_intent",
                    "arguments": json.dumps(
                        {"intent": "approve", "evidence": "确认取消"}
                    ),
                },
            }
        ],
    )
    monkeypatch.setattr(
        "app.agent.confirmation_intent.request_model",
        lambda *args: intent,
    )

    second = chat(
        api_client,
        "确认取消",
        session_id=session_id,
        thread_id=thread_id,
    )
    assert second.status_code == 200
    assert second.json()["pending_action"] is None
    assert "已取消" in second.json()["reply"]
    assert read_database(
        "SELECT status FROM orders WHERE order_id = 'ORD-API-1'"
    )[0]["status"] == "canceled"
    assert len(read_database("SELECT * FROM operation_logs")) == 1
    assert read_database("SELECT * FROM refund_requests") == []


def test_chat_returns_reply_when_history_save_fails(api_client, monkeypatch):
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                CREATE TRIGGER fail_chat_save BEFORE INSERT ON chat_threads
                BEGIN SELECT RAISE(ABORT, 'test history unavailable'); END
                """
            )
    finally:
        conn.close()

    monkeypatch.setattr(
        "app.harness.runtime.request_model",
        lambda *args: text_reply("查询完成。"),
    )

    response = chat(api_client, "你好")
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "查询完成。"
    assert body["history_saved"] is False
    assert "历史未保存" in body["warning"]
    assert read_database("SELECT * FROM chat_threads") == []
