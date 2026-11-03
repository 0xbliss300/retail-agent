"""工具执行边界：超时、数据库忙、异常归一化和只读/写工具的重试差异。"""

import json
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from openai.types.chat import ChatCompletionMessage

from app.harness import tool_executor
from app.harness.runtime import create_messages, run_agent
from app.storage import database


def tool_call(name: str, arguments: dict, call_id: str = "call_test") -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments, ensure_ascii=False),
        ),
    )


def model_tool_reply(name: str, arguments: dict) -> ChatCompletionMessage:
    return ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[
            {
                "id": "call_test",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        ],
    )


@pytest.fixture(autouse=True)
def short_timeout(monkeypatch):
    monkeypatch.setattr(tool_executor, "TOOL_TIMEOUT_SECONDS", 0.05)


@pytest.fixture
def temp_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()


def _count(table: str) -> int:
    conn = database.get_connection()
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_read_tool_timeout_is_retried_once(monkeypatch):
    calls = []

    def slow_get_order(**kwargs):
        calls.append(kwargs)
        time.sleep(0.2)
        return {"ok": True, "order": {"order_id": "ORD-1"}}

    monkeypatch.setattr(tool_executor, "get_order", slow_get_order)

    result = tool_executor.execute_tool(
        tool_call("get_order", {"order_id": "ORD-1"}),
        "USER-001",
    )

    assert result == {"ok": False, "error": "TOOL_TIMEOUT"}
    assert len(calls) == 2


def test_read_tool_retries_after_database_busy(monkeypatch):
    calls = []

    def busy_once(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return {"ok": True, "order": {"order_id": "ORD-1"}}

    monkeypatch.setattr(tool_executor, "get_order", busy_once)

    result = tool_executor.execute_tool(
        tool_call("get_order", {"order_id": "ORD-1"}),
        "USER-001",
    )

    assert result["ok"] is True
    assert len(calls) == 2


def test_write_tool_timeout_is_not_retried_and_does_not_write(temp_database, monkeypatch):
    calls = []

    def slow_prepare(**kwargs):
        calls.append(kwargs)
        time.sleep(0.2)
        return {"ok": True, "status": "awaiting_confirmation"}

    monkeypatch.setattr(tool_executor, "prepare_cancel_confirmation", slow_prepare)

    before = {table: _count(table) for table in ("orders", "confirmations", "operation_logs")}
    result = tool_executor.execute_tool(
        tool_call("cancel_order", {"order_id": "ORD-1", "reason": "不想要了"}),
        "USER-001",
        session_id="sess_test",
        thread_id="thread_test",
    )

    assert result == {"ok": False, "error": "TOOL_TIMEOUT"}
    assert len(calls) == 1
    assert {table: _count(table) for table in before} == before


def test_unexpected_exception_is_normalized_without_retry(monkeypatch):
    calls = []

    def broken(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("boom")

    monkeypatch.setattr(tool_executor, "get_order", broken)

    result = tool_executor.execute_tool(
        tool_call("get_order", {"order_id": "ORD-1"}),
        "USER-001",
    )

    assert result == {"ok": False, "error": "TOOL_ERROR"}
    assert len(calls) == 1


def test_runtime_returns_fixed_answer_for_single_tool_timeout(monkeypatch):
    messages = create_messages("测试提示词")
    response = model_tool_reply("get_order", {"order_id": "ORD-1"})

    with patch("app.harness.runtime.request_model", return_value=response) as model, patch(
        "app.harness.runtime.execute_tool",
        return_value={"ok": False, "error": "TOOL_TIMEOUT"},
    ):
        answer = run_agent("查订单", "USER-001", messages, None)

    assert "超时" in answer
    assert "未确认" in answer
    assert model.call_count == 1
    assert messages[-1] == {"role": "assistant", "content": answer}