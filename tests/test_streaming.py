"""流式模型与 SSE 接口：增量文本、工具调用缓冲和待确认操作。"""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openai.types.chat import ChatCompletionMessage

from app import main
from app.agent.model_client import stream_model
from app.business.service import check_cancel_eligibility
from app.storage import database, persistence, seed


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
def api_client(tmp_path, monkeypatch, login_demo):
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

    monkeypatch.setattr(main, "create_client", lambda: None)
    with TestClient(main.app) as client:
        login_demo(client)
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


@pytest.mark.parametrize("outcome", ["approve", "reject", "expire"])
def test_chat_stream_answers_followups_before_deciding(api_client, monkeypatch, outcome):
    def tool_call(name, **arguments):
        return {
            "id": f"call_{name}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }

    def stream_reply(text=None, calls=None):
        if text:
            yield "token", text
        yield "message", ChatCompletionMessage(role="assistant", content=text, tool_calls=calls)

    order_id = "ORD-STREAM-1"
    cancel_call = tool_call("cancel_order", order_id=order_id, reason="不需要了")
    monkeypatch.setattr(
        "app.harness.runtime.stream_model", lambda *_: stream_reply(calls=[cancel_call]),
    )

    def send(text, **scope):
        response = api_client.post("/chat/stream", json={"message": text, **scope})
        assert response.status_code == 200
        events = parse_sse(response.text)
        done = next(data for event, data in events if event == "done")
        assert "".join(data["text"] for event, data in events if event == "token") == done["reply"]
        return done

    prepared = send(f"取消 {order_id}，不需要了")
    pending = prepared["pending_action"]
    scope = {key: prepared[key] for key in ("session_id", "thread_id")}
    order_status = "SELECT status FROM orders WHERE order_id = ?"
    confirmations = "SELECT confirmation_id, status, expires_at FROM confirmations WHERE order_id = ?"
    original_confirmation = read_database(confirmations, (order_id,))[0]
    expires_at = datetime.fromisoformat(pending["expires_at"])
    clock = expires_at - timedelta(minutes=1)

    class TestDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock

    monkeypatch.setattr(main, "datetime", TestDatetime)
    monkeypatch.setattr("app.harness.runtime.datetime", TestDatetime)
    monkeypatch.setattr("app.harness.conversation.datetime", TestDatetime)

    def forbid_intent(*args):
        pytest.fail("插问进入了确认意图分类")

    monkeypatch.setattr("app.agent.confirmation_intent.request_model", forbid_intent)
    question = "确认取消后会退款吗？"
    blocked_cancel = {**cancel_call, "id": "call_cancel_during_followup"}
    amount_lookup = tool_call(
        "find_orders_by_amount", paid_amount_cents=9900, purpose="cancel_order",
    )
    read_order = tool_call("get_order", order_id=order_id)

    def answer_question(client, messages, tools):
        assert {tool["function"]["name"] for tool in tools} == {
            "get_order", "get_logistics", "get_refund_status", "get_policy",
        }
        current_question = json.loads([m for m in messages if m["role"] == "user"][-1]["content"])
        assert current_question["pending_action"]["order_id"] == order_id
        assert current_question["pending_action"]["summary"] == pending["summary"]
        if current_question["current_user_message"] != question:
            assert any(m.get("content") == question for m in messages)
            yield from stream_reply("这笔订单当前待付款。")
        elif messages[-1]["role"] == "user":
            yield from stream_reply("已取消。", [blocked_cancel, amount_lookup, read_order])
        else:
            rejected_cancel, rejected_lookup, order = [
                json.loads(m["content"]) for m in messages[-3:]
            ]
            assert rejected_cancel["error"] == rejected_lookup["error"] == "PENDING_CONFIRMATION_TOOL_NOT_ALLOWED"
            assert order["order"]["status"] == "unpaid"
            yield from stream_reply("这笔订单待付款，取消后无需退款。")

    monkeypatch.setattr("app.harness.runtime.stream_model", answer_question)
    first = send(question, **scope)
    assert first["pending_action"] == pending
    assert first["reply"] == "这笔订单待付款，取消后无需退款。"

    def answer_last_question(*args):
        nonlocal clock
        if outcome == "expire":
            clock = expires_at
        yield from answer_question(*args)

    monkeypatch.setattr("app.harness.runtime.stream_model", answer_last_question)
    second = send("那这笔订单现在是待付款吗？", **scope)
    assert second["reply"] == "这笔订单当前待付款。"
    assert second["pending_action"] == (None if outcome == "expire" else pending)
    expected_status = "expired" if outcome == "expire" else "pending"
    assert read_database(confirmations, (order_id,)) == [
        {**original_confirmation, "status": expected_status},
    ]
    assert read_database(order_status, (order_id,)) == [{"status": "unpaid"}]
    assert read_database("SELECT * FROM operation_logs") == []

    response = api_client.post("/confirm", json={
        **scope, "confirmation_id": pending["confirmation_id"],
        "decision": "reject" if outcome == "reject" else "approve",
    })
    assert response.status_code == 200
    assert response.json()["pending_action"] is None
    assert read_database(order_status, (order_id,)) == [
        {"status": "canceled" if outcome == "approve" else "unpaid"},
    ]
    final_status = {"approve": "consumed", "reject": "rejected", "expire": "expired"}
    assert read_database(confirmations, (order_id,)) == [
        {**original_confirmation, "status": final_status[outcome]},
    ]
    assert len(read_database("SELECT * FROM operation_logs")) == (1 if outcome == "approve" else 0)
    assert read_database("SELECT * FROM refund_requests WHERE order_id = ?", (order_id,)) == []


def test_chat_stream_restores_order_clarification_without_switching_confirmation(api_client, monkeypatch):
    pending_id, queried_id = "ORD-STREAM-1", "ORD-2002"
    seed.seed_orders()
    seed.seed_logistics()

    def model_tool(name, **args):
        return ChatCompletionMessage(role="assistant", tool_calls=[{
            "id": f"call_{name}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }])

    fake_turns = iter([
        model_tool("cancel_order", order_id=pending_id, reason="不需要了"),
        model_tool("get_logistics", order_id=queried_id),
        ChatCompletionMessage(role="assistant", content=f"订单 {queried_id} 的物流已查到。"),
    ])

    def fake_stream(client, messages, tools):
        if messages[-1]["role"] == "tool":
            assert json.loads(messages[-1]["content"])["order_id"] == queried_id
        yield "message", next(fake_turns)

    def forbid_text_confirmation(*args):
        pytest.fail("两笔订单有歧义时，文字确认进入了模型分类")

    checked = []

    def check_target(**arguments):
        checked.append((arguments["user_id"], arguments["order_id"]))
        return check_cancel_eligibility(**arguments)

    monkeypatch.setattr("app.harness.runtime.stream_model", fake_stream)
    monkeypatch.setattr("app.harness.runtime.classify_confirmation_intent", forbid_text_confirmation)
    monkeypatch.setattr("app.harness.runtime.check_cancel_eligibility", check_target)

    def send(message, **scope):
        response = api_client.post("/chat/stream", json={"message": message, **scope})
        assert response.status_code == 200
        return next(data for event, data in parse_sse(response.text) if event == "done")

    prepared = send(f"取消 {pending_id}，不需要了")
    scope = {key: prepared[key] for key in ("session_id", "thread_id")}
    pending = prepared["pending_action"]
    assert pending["order_id"] == pending_id
    assert send(f"查 {queried_id} 的物流", **scope)["pending_action"] == pending

    question = send("这笔还能取消吗？", **scope)
    assert all(order_id in question["reply"] for order_id in (pending_id, queried_id))
    assert question["pending_action"] == pending
    assert checked == []
    snapshot = persistence.load_conversation(user_id="USER-001", **scope)
    assert snapshot["last_queried_order_id"] == queried_id
    assert snapshot["pending_clarification"]["confirmation_id"] == pending["confirmation_id"]

    recovered = api_client.post("/recover", json=scope).json()
    assert recovered["confirmation_id"] == pending["confirmation_id"]
    answer = send("刚查物流的那笔", **scope)
    assert queried_id in answer["reply"] and "已发货，不能取消" in answer["reply"]
    assert checked == [("USER-001", queried_id)]
    assert answer["pending_action"] == pending
    assert persistence.load_conversation(user_id="USER-001", **scope)["pending_clarification"] is None

    text = send("确认取消", **scope)
    assert "确认卡" in text["reply"] and pending_id in text["reply"]
    assert text["pending_action"] == pending
    assert read_database("SELECT * FROM operation_logs") == []

    approved = api_client.post("/confirm", json={
        **scope, "confirmation_id": pending["confirmation_id"], "decision": "approve",
    })
    assert approved.status_code == 200
    assert approved.json()["cancellation_result"]["order_id"] == pending_id
    assert read_database("SELECT status FROM orders WHERE order_id = ?", (queried_id,)) == [{"status": "shipped"}]
    assert read_database("SELECT status FROM orders WHERE order_id = ?", (pending_id,)) == [{"status": "canceled"}]
    assert read_database("SELECT order_id FROM operation_logs") == [{"order_id": pending_id}]


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
