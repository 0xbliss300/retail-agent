"""CLI 自动保存完整轮次；历史保存失败不重做业务操作。"""

import json
from datetime import datetime, timezone

import pytest
from openai.types.chat import ChatCompletionMessage

from app.harness.confirmation import prepare_refund_confirmation
from app.storage import database, persistence
from scripts import demo_chat


@pytest.fixture
def chat_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()
    monkeypatch.setattr(demo_chat, "create_client", lambda: None)


def saved_threads():
    conn = database.get_connection()
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM chat_threads ORDER BY rowid")]
    finally:
        conn.close()


def install_inputs(monkeypatch, values):
    inputs = iter(values)
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))


def text_reply(content):
    return ChatCompletionMessage(role="assistant", content=content)


def fail_history_inserts():
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                CREATE TRIGGER fail_chat_save BEFORE INSERT ON chat_threads
                BEGIN
                    SELECT RAISE(ABORT, 'test history unavailable');
                END
                """
            )
    finally:
        conn.close()


def test_each_completed_turn_saves_full_tool_history(chat_db, monkeypatch):
    tool_reply = ChatCompletionMessage(
        role="assistant", content=None,
        tool_calls=[{
            "id": "call_autosave_order", "type": "function",
            "function": {"name": "get_order", "arguments": '{"order_id":"ORD-1001"}'},
        }],
    )
    responses = iter([tool_reply, text_reply("订单已支付。"), text_reply("99元。")])
    monkeypatch.setattr(
        "app.harness.runtime.request_model", lambda client, messages, tools: next(responses),
    )
    install_inputs(monkeypatch, ["查 ORD-1001", "这个订单多少钱", "exit"])

    demo_chat.main([])

    rows = saved_threads()
    assert len(rows) == 1
    assert rows[0]["user_id"] == "USER-001"
    assert rows[0]["title"] == "查 ORD-1001"
    assert rows[0]["version"] == 2
    messages = json.loads(rows[0]["messages_json"])
    assert [m["role"] for m in messages] == [
        "system", "user", "assistant", "tool", "assistant", "user", "assistant",
    ]
    assert messages[2]["tool_calls"][0]["id"] == "call_autosave_order"
    assert messages[3]["tool_call_id"] == "call_autosave_order"
    assert json.loads(messages[3]["content"])["order"]["order_id"] == "ORD-1001"
    assert messages[-1]["content"] == "99元。"


def test_exit_without_question_does_not_save_empty_conversation(chat_db, monkeypatch):
    install_inputs(monkeypatch, ["exit"])
    demo_chat.main([])
    assert saved_threads() == []


def test_failed_model_turn_preserves_last_completed_snapshot(chat_db, monkeypatch):
    def model(client, messages, tools):
        if sum(m["role"] == "user" for m in messages) == 1:
            return text_reply("请提供订单号。")
        raise RuntimeError("test model unavailable")

    monkeypatch.setattr("app.harness.runtime.request_model", model)
    install_inputs(monkeypatch, ["帮我查订单", "ORD-1001"])

    # 本轮不改变模型请求异常的既有行为，只验证它不会覆盖上一轮快照。
    with pytest.raises(RuntimeError, match="test model unavailable"):
        demo_chat.main([])

    rows = saved_threads()
    assert len(rows) == 1
    assert rows[0]["version"] == 1
    messages = json.loads(rows[0]["messages_json"])
    assert messages[-1]["content"] == "请提供订单号。"
    assert not any(m.get("content") == "ORD-1001" for m in messages)


def test_save_conflict_keeps_newer_snapshot_and_stops_chat(chat_db, monkeypatch, capsys):
    real_save = persistence.save_conversation

    def competing_save(**kwargs):
        rival = {**kwargs, "messages": [*kwargs["messages"][:-1], {
            "role": "assistant", "content": "另一个窗口保存的回答。",
        }]}
        real_save(**rival)
        return real_save(**kwargs)

    monkeypatch.setattr(demo_chat, "save_conversation", competing_save, raising=False)
    monkeypatch.setattr(
        "app.harness.runtime.request_model", lambda client, messages, tools: text_reply("当前窗口的回答。"),
    )
    install_inputs(monkeypatch, ["查询订单"])

    demo_chat.main([])

    rows = saved_threads()
    assert len(rows) == 1
    assert rows[0]["version"] == 1
    assert json.loads(rows[0]["messages_json"])[-1]["content"] == "另一个窗口保存的回答。"
    output = capsys.readouterr().out
    assert "当前窗口的回答。" in output
    assert "历史未保存" in output


def test_database_save_failure_reports_answer_without_retrying(chat_db, monkeypatch, capsys):
    fail_history_inserts()
    monkeypatch.setattr(
        "app.harness.runtime.request_model", lambda client, messages, tools: text_reply("订单已支付。"),
    )
    install_inputs(monkeypatch, ["查订单"])

    demo_chat.main([])

    assert saved_threads() == []
    output = capsys.readouterr().out
    assert "订单已支付。" in output
    assert "历史未保存" in output


def test_refund_commit_survives_history_save_failure(chat_db, monkeypatch, capsys):
    now = datetime.now(timezone.utc)
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO orders (order_id,user_id,status,paid_amount_cents,shipped_at)
                VALUES ('ORD-SAVE-FAIL','USER-001','shipped',8900,?)
                """, (now.isoformat(),),
            )
    finally:
        conn.close()
    pending = prepare_refund_confirmation(
        user_id="USER-001", session_id="sess_save_fail", thread_id="thread_save_fail",
        order_id="ORD-SAVE-FAIL", reason="商品破损", now=now,
    )
    assert pending["status"] == "awaiting_confirmation"
    fail_history_inserts()
    response = ChatCompletionMessage(
        role="assistant", content=None,
        tool_calls=[{
            "id": "call_save_fail", "type": "function",
            "function": {
                "name": "report_confirmation_intent",
                "arguments": json.dumps({"intent": "approve", "evidence": "确认提交"}),
            },
        }],
    )
    monkeypatch.setattr(
        "app.agent.confirmation_intent.request_model", lambda client, messages, tools: response,
    )
    install_inputs(monkeypatch, ["1", "确认提交"])

    demo_chat.main([])

    conn = database.get_connection()
    try:
        refunds = [dict(row) for row in conn.execute("SELECT * FROM refund_requests")]
        status = conn.execute(
            "SELECT status FROM confirmations WHERE confirmation_id=?", (pending["confirmation_id"],),
        ).fetchone()[0]
    finally:
        conn.close()
    assert status == "consumed"
    assert len(refunds) == 1
    assert refunds[0]["status"] == "pending"
    assert saved_threads() == []
    output = capsys.readouterr().out
    assert refunds[0]["refund_id"] in output
    assert "历史未保存" in output


def test_pending_resume_loads_existing_history_with_current_system_prompt(chat_db, monkeypatch):
    now = datetime.now(timezone.utc)
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO orders (order_id,user_id,status,paid_amount_cents,shipped_at)
                VALUES ('ORD-HISTORY-PENDING','USER-001','shipped',8900,?)
                """, (now.isoformat(),),
            )
    finally:
        conn.close()
    context = {"user_id": "USER-001", "session_id": "sess_saved", "thread_id": "thread_saved"}
    pending = prepare_refund_confirmation(
        **context, order_id="ORD-HISTORY-PENDING", reason="商品破损", now=now,
    )
    assert pending["status"] == "awaiting_confirmation"
    original = [
        {"role": "system", "content": "old system prompt"},
        {"role": "user", "content": "ORD-HISTORY-PENDING 商品破损，申请退款"},
        {"role": "assistant", "content": pending["summary"]},
    ]
    persistence.save_conversation(**context, messages=original, now=now, expected_version=0)
    response = ChatCompletionMessage(
        role="assistant", content=None,
        tool_calls=[{
            "id": "call_wait", "type": "function",
            "function": {
                "name": "report_confirmation_intent",
                "arguments": json.dumps({"intent": "other", "evidence": "先等等"}),
            },
        }],
    )
    monkeypatch.setattr(
        "app.agent.confirmation_intent.request_model", lambda client, messages, tools: response,
    )
    install_inputs(monkeypatch, ["1", "先等等", "exit"])

    demo_chat.main([])

    loaded = persistence.load_conversation(**context)
    assert loaded["version"] == 2
    assert loaded["messages"][1:3] == original[1:]
    assert loaded["messages"][0]["content"] == demo_chat.build_system_prompt(
        demo_chat.ORDER_PROMPT, demo_chat.POLICY_PROMPT, demo_chat.REFUND_PROMPT,
    )
    assert loaded["messages"][-2] == {"role": "user", "content": "先等等"}
