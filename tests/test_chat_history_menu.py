"""按标题恢复聊天历史：用户隔离、确认状态及独立进程验证。"""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from openai.types.chat import ChatCompletionMessage

from app.business.service import approve_refund_confirmation
from app.harness.confirmation import prepare_refund_confirmation
from app.storage import database, persistence
from scripts import demo_chat


@pytest.fixture
def history_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()
    clock = {"now": datetime(2026, 10, 31, 2, tzinfo=UTC)}

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = clock["now"]
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(demo_chat, "datetime", FixedDatetime)
    monkeypatch.setattr("app.harness.runtime.datetime", FixedDatetime)
    monkeypatch.setattr(demo_chat, "create_client", lambda: None)
    return clock


def save_history(
    now, *, owner="USER-001", session="sess_history", thread="thread_history", title="查 ORD-1001",
):
    context = {"user_id": owner, "session_id": session, "thread_id": thread}
    messages = [
        {"role": "system", "content": "old system prompt"},
        {"role": "user", "content": title},
        {"role": "assistant", "content": "订单已支付，实付99元。"},
    ]
    persistence.save_conversation(**context, messages=messages, now=now, expected_version=0)
    return context


def inputs(monkeypatch, values):
    values = iter(values)
    monkeypatch.setattr("builtins.input", lambda prompt: next(values))


def no_model(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("仅选择或退出不能请求模型")
    monkeypatch.setattr("app.harness.runtime.request_model", forbidden)
    monkeypatch.setattr("app.agent.confirmation_intent.request_model", forbidden)


def create_pending(context, now):
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO orders (order_id,user_id,status,paid_amount_cents,shipped_at)
                VALUES ('ORD-HISTORY','USER-001','shipped',8900,?)
                """, (now.isoformat(),),
            )
    finally:
        conn.close()
    result = prepare_refund_confirmation(**context, order_id="ORD-HISTORY", reason="商品破损", now=now)
    assert result["status"] == "awaiting_confirmation"
    return result["confirmation_id"]


def test_list_conversations_is_scoped_sorted_and_contains_no_message_bodies(history_db):
    now = history_db["now"]
    save_history(now, session="older", thread="older", title="较早的问题")
    save_history(now + timedelta(minutes=1), session="newer", thread="newer", title="较新的问题")
    save_history(now, owner="OTHER", session="private", thread="private", title="私人问题")

    records = persistence.list_conversations(user_id="USER-001")

    assert [row["title"] for row in records] == ["较新的问题", "较早的问题"]
    assert records[0]["session_id"] == "newer"
    assert records[0]["thread_id"] == "newer"
    assert all("messages" not in row and "messages_json" not in row for row in records)


def test_history_only_conversation_restores_context_and_saves_next_version(history_db, monkeypatch, capsys):
    context = save_history(history_db["now"])
    inputs(monkeypatch, ["1", "这个订单多少钱？", "exit"])

    def model(client, messages, tools):
        assert messages[0]["content"] == demo_chat.build_system_prompt(
            demo_chat.ORDER_PROMPT, demo_chat.POLICY_PROMPT, demo_chat.REFUND_PROMPT,
        )
        assert messages[1] == {"role": "user", "content": "查 ORD-1001"}
        assert messages[2]["content"] == "订单已支付，实付99元。"
        assert messages[-1]["content"] == "这个订单多少钱？"
        return ChatCompletionMessage(role="assistant", content="实付99元。")

    monkeypatch.setattr("app.harness.runtime.request_model", model)

    demo_chat.main([])

    loaded = persistence.load_conversation(**context)
    assert loaded["version"] == 2
    assert loaded["messages"][-1]["content"] == "实付99元。"
    assert "查 ORD-1001" in capsys.readouterr().out


def test_restored_tool_calls_and_results_reach_model_intact(history_db, monkeypatch):
    context = {"user_id": "USER-001", "session_id": "sess_tools", "thread_id": "thread_tools"}
    messages = [
        {"role": "system", "content": "old system prompt"},
        {"role": "user", "content": "查 ORD-1001"},
        {
            "role": "assistant", "content": None,
            "tool_calls": [{
                "id": "call_saved_order", "type": "function",
                "function": {"name": "get_order", "arguments": '{"order_id":"ORD-1001"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call_saved_order", "content": '{"ok":true}'},
        {"role": "assistant", "content": "订单已支付。"},
    ]
    persistence.save_conversation(**context, messages=messages, now=history_db["now"], expected_version=0)
    inputs(monkeypatch, ["1", "继续", "exit"])

    def model(client, restored, tools):
        assert restored[1:5] == messages[1:]
        assert restored[-1] == {"role": "user", "content": "继续"}
        return ChatCompletionMessage(role="assistant", content="请说明还想查询什么。")

    monkeypatch.setattr("app.harness.runtime.request_model", model)

    demo_chat.main([])

    assert persistence.load_conversation(**context)["version"] == 2


def test_start_new_does_not_load_or_overwrite_selected_history(history_db, monkeypatch):
    context = save_history(history_db["now"])
    before = persistence.load_conversation(**context)
    inputs(monkeypatch, ["0", "新问题", "exit"])

    def model(client, messages, tools):
        assert [m["role"] for m in messages] == ["system", "user"]
        assert messages[-1]["content"] == "新问题"
        return ChatCompletionMessage(role="assistant", content="新的回答。")

    monkeypatch.setattr("app.harness.runtime.request_model", model)

    demo_chat.main([])

    assert persistence.load_conversation(**context) == before
    conn = database.get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM chat_threads").fetchone()[0] == 2
    finally:
        conn.close()


def test_foreign_history_is_not_displayed_or_resumed(history_db, monkeypatch, capsys):
    context = save_history(history_db["now"], owner="OTHER", title="其他人的私人问题")
    before = persistence.load_conversation(**context)
    inputs(monkeypatch, ["exit"])
    no_model(monkeypatch)

    demo_chat.main([])

    assert "其他人的私人问题" not in capsys.readouterr().out
    assert persistence.load_conversation(**context) == before


def test_history_and_pending_operation_are_one_menu_entry(history_db, monkeypatch, capsys):
    context = save_history(history_db["now"], title="申请 ORD-HISTORY 退款")
    confirmation_id = create_pending(context, history_db["now"])
    inputs(monkeypatch, ["2", "1", "exit"])
    no_model(monkeypatch)

    demo_chat.main([])

    output = capsys.readouterr().out
    assert "申请 ORD-HISTORY 退款" in output
    assert "请选择列表中的有效序号" in output
    assert not any(line.startswith("2. ") for line in output.splitlines())
    assert persistence.load_conversation(**context)["version"] == 1
    conn = database.get_connection()
    try:
        assert conn.execute(
            "SELECT status FROM confirmations WHERE confirmation_id=?", (confirmation_id,),
        ).fetchone()[0] == "pending"
        assert conn.execute("SELECT COUNT(*) FROM refund_requests").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize("expire_during_selection", [False, True])
def test_history_survives_expired_confirmation_without_executing_it(
    history_db, monkeypatch, capsys, expire_during_selection,
):
    context = save_history(history_db["now"])
    confirmation_id = create_pending(context, history_db["now"])
    before = persistence.load_conversation(**context)
    if not expire_during_selection:
        history_db["now"] += timedelta(minutes=10)
    choices = iter(["1", "exit"])

    def choose(prompt):
        value = next(choices)
        if value == "1" and expire_during_selection:
            history_db["now"] += timedelta(minutes=10)
        return value

    monkeypatch.setattr("builtins.input", choose)
    no_model(monkeypatch)

    demo_chat.main([])

    assert "已加载之前保存的对话消息" in capsys.readouterr().out
    assert persistence.load_conversation(**context) == before
    conn = database.get_connection()
    try:
        assert conn.execute(
            "SELECT status FROM confirmations WHERE confirmation_id=?", (confirmation_id,),
        ).fetchone()[0] == "expired"
        assert conn.execute("SELECT COUNT(*) FROM refund_requests").fetchone()[0] == 0
    finally:
        conn.close()


def test_loading_completed_refund_history_does_not_replay_operation(history_db, monkeypatch):
    context = save_history(history_db["now"])
    confirmation_id = create_pending(context, history_db["now"])
    result = approve_refund_confirmation(**context, confirmation_id=confirmation_id, now=history_db["now"])
    assert result["ok"] is True
    conn = database.get_connection()
    try:
        before = list(conn.iterdump())
        inputs(monkeypatch, ["1", "exit"])
        no_model(monkeypatch)

        demo_chat.main([])

        assert list(conn.iterdump()) == before
    finally:
        conn.close()


def test_unreadable_snapshot_is_not_silently_replaced(history_db, monkeypatch, capsys):
    save_history(history_db["now"])
    conn = database.get_connection()
    try:
        with conn:
            conn.execute("UPDATE chat_threads SET messages_json = 'broken json'")
        before = list(conn.iterdump())
        inputs(monkeypatch, ["1"])
        no_model(monkeypatch)

        demo_chat.main([])

        assert list(conn.iterdump()) == before
        assert "无法读取" in capsys.readouterr().out
    finally:
        conn.close()


def test_chat_history_survives_two_independent_python_processes(history_db):
    code = '''
import builtins, sys
from pathlib import Path
from openai.types.chat import ChatCompletionMessage
from app.storage import database
from app.harness import runtime
from scripts import demo_chat
database.DB_PATH = Path(sys.argv[1])
phase = sys.argv[2]
demo_chat.create_client = lambda: None
answers = iter(["查 ORD-1001", "exit"] if phase == "first" else ["1", "实付多少", "exit"])
builtins.input = lambda prompt: next(answers)
def offline_model(client, messages, tools):
    if phase == "second":
        assert messages[1]["content"] == "查 ORD-1001"
        assert messages[2]["content"] == "订单已支付，实付99元。"
    return ChatCompletionMessage(role="assistant", content="订单已支付，实付99元。")
runtime.request_model = offline_model
demo_chat.main([])
'''
    for phase in ("first", "second"):
        result = subprocess.run(
            [sys.executable, "-c", code, str(database.DB_PATH), phase],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    conn = database.get_connection()
    try:
        rows = conn.execute("SELECT version, messages_json FROM chat_threads").fetchall()
        assert len(rows) == 1
        assert rows[0]["version"] == 2
        assert json.loads(rows[0]["messages_json"])[-2]["content"] == "实付多少"
    finally:
        conn.close()
