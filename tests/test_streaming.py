"""流式模型与 SSE 接口：增量文本、工具调用缓冲和待确认操作。"""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openai.types.chat import ChatCompletionMessage

from app import main
from app.agent.model_client import stream_model
from app.storage import database


def chunk(content=None, tool_calls=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=tool_calls)
            )
        ]
    )


def tool_delta(index, *, id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class FakeClient:
    def __init__(self, chunks):
        self._chunks = chunks

    @property
    def chat(self):
        return SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    def _create(self, **kwargs):
        assert kwargs["stream"] is True
        return iter(self._chunks)


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
                VALUES ('ORD-STREAM-1', 'USER-001', 'unpaid', 0)
                """
            )
    finally:
        conn.close()

    monkeypatch.setattr(main, "DEMO_USER_ID", "USER-001")
    monkeypatch.setattr(main, "create_client", lambda: None)
    with TestClient(main.app) as client:
        yield client


def parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        if not lines:
            continue
        event = next(line[7:] for line in lines if line.startswith("event: "))
        data = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
        events.append((event, data))
    return events


def read_database(query, parameters=()):
    conn = database.get_connection()
    try:
        return [dict(row) for row in conn.execute(query, parameters)]
    finally:
        conn.close()


def test_stream_model_accumulates_content_chunks(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test-model")
    client = FakeClient([chunk("你"), chunk("好"), chunk(None)])

    events = list(stream_model(client, [], []))

    assert events[:-1] == [("token", "你"), ("token", "好")]
    assert events[-1][0] == "message"
    assert events[-1][1].content == "你好"
    assert events[-1][1].tool_calls is None


def test_stream_model_buffers_tool_call_chunks(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test-model")
    client = FakeClient(
        [
            chunk(
                tool_calls=[
                    tool_delta(
                        0,
                        id="call_1",
                        name="cancel_order",
                        arguments='{"order_id":"',
                    )
                ]
            ),
            chunk(
                tool_calls=[
                    tool_delta(0, arguments='ORD-1","reason":"不需要了"}'),
                ]
            ),
        ]
    )

    events = list(stream_model(client, [], []))

    message = events[-1][1]
    assert message.content is None
    call = message.tool_calls[0]
    assert call.id == "call_1"
    assert call.function.name == "cancel_order"
    assert call.function.arguments == '{"order_id":"ORD-1","reason":"不需要了"}'


def test_chat_stream_outputs_tokens_and_saves_history(api_client, monkeypatch):
    def fake_stream(client, messages, tools):
        yield "token", "你"
        yield "token", "好"
        yield "message", ChatCompletionMessage(role="assistant", content="你好")

    monkeypatch.setattr("app.harness.runtime.stream_model", fake_stream)

    response = api_client.post("/chat/stream", json={"message": "你好"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    tokens = [data["text"] for event, data in events if event == "token"]
    assert "".join(tokens) == "你好"

    done = next(data for event, data in events if event == "done")
    assert done["reply"] == "你好"
    assert done["history_saved"] is True
    assert done["warning"] is None
    assert done["pending_action"] is None
    assert done["session_id"].startswith("sess_")

    rows = read_database("SELECT * FROM chat_threads")
    assert len(rows) == 1
    assert rows[0]["version"] == 1
    assert json.loads(rows[0]["messages_json"])[-1]["content"] == "你好"


def test_chat_stream_prepares_then_confirms_a_cancel(api_client, monkeypatch):
    def fake_stream(client, messages, tools):
        yield "message", ChatCompletionMessage(
            role="assistant",
            content=None,
            tool_calls=[
                {
                    "id": "call_cancel",
                    "type": "function",
                    "function": {
                        "name": "cancel_order",
                        "arguments": json.dumps(
                            {"order_id": "ORD-STREAM-1", "reason": "不需要了"}
                        ),
                    },
                }
            ],
        )

    monkeypatch.setattr("app.harness.runtime.stream_model", fake_stream)

    first = api_client.post(
        "/chat/stream",
        json={"message": "取消 ORD-STREAM-1，不需要了"},
    )
    assert first.status_code == 200
    first_done = next(
        data for event, data in parse_sse(first.text) if event == "done"
    )
    assert first_done["pending_action"]["tool"] == "cancel_order"
    assert read_database(
        "SELECT status FROM orders WHERE order_id = 'ORD-STREAM-1'"
    )[0]["status"] == "unpaid"

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

    second = api_client.post(
        "/chat/stream",
        json={
            "message": "确认取消",
            "session_id": first_done["session_id"],
            "thread_id": first_done["thread_id"],
        },
    )
    assert second.status_code == 200
    second_done = next(
        data for event, data in parse_sse(second.text) if event == "done"
    )
    assert second_done["pending_action"] is None
    assert "已取消" in second_done["reply"]
    assert read_database(
        "SELECT status FROM orders WHERE order_id = 'ORD-STREAM-1'"
    )[0]["status"] == "canceled"
    assert len(read_database("SELECT * FROM operation_logs")) == 1


def test_chat_stream_reports_history_failure(api_client, monkeypatch):
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                CREATE TRIGGER fail_stream_save BEFORE INSERT ON chat_threads
                BEGIN SELECT RAISE(ABORT, 'test stream history unavailable'); END
                """
            )
    finally:
        conn.close()

    def fake_stream(client, messages, tools):
        yield "token", "查询完成"
        yield "message", ChatCompletionMessage(role="assistant", content="查询完成")

    monkeypatch.setattr("app.harness.runtime.stream_model", fake_stream)

    response = api_client.post("/chat/stream", json={"message": "你好"})

    assert response.status_code == 200
    done = next(data for event, data in parse_sse(response.text) if event == "done")
    assert done["reply"] == "查询完成"
    assert done["history_saved"] is False
    assert "历史未保存" in done["warning"]
    assert read_database("SELECT * FROM chat_threads") == []