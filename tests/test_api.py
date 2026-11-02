"""HTTP API：健康检查、会话保存、待确认操作和错误路径。"""

import json

import pytest
from fastapi.testclient import TestClient
from openai.types.chat import ChatCompletionMessage

from app import main
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
def api_client(tmp_path, monkeypatch):
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

    monkeypatch.setattr(main, "DEMO_USER_ID", "USER-001")
    monkeypatch.setattr(main, "create_client", lambda: None)
    with TestClient(main.app) as client:
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
        "demo_user_id": "USER-001",
        "version": main.APP_VERSION,
    }


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