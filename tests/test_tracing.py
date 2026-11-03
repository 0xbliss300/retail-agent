"""Trace 事件写入、脱敏和失败隔离。"""

import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from openai.types.chat import ChatCompletionMessage

from app.harness import tool_executor
from app.harness.conversation import new_conversation, send_message
from app.harness.runtime import run_agent, stream_agent
from app.observability.tracing import emit_trace
from app.storage import database


def tool_call(name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id="call_test",
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


def read_events(path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


@pytest.fixture
def trace_path(tmp_path, monkeypatch):
    path = tmp_path / "traces.jsonl"
    monkeypatch.setenv("TRACE_ENABLED", "true")
    monkeypatch.setenv("TRACE_PATH", str(path))
    return path


@pytest.fixture
def temp_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()


def test_emit_trace_appends_jsonl(trace_path):
    emit_trace("custom.event", run_id="run_1", value=1)
    emit_trace("custom.event", run_id="run_1", value=2)

    events = read_events(trace_path)

    assert len(events) == 2
    assert events[0]["event"] == "custom.event"
    assert events[0]["run_id"] == "run_1"
    assert events[0]["value"] == 1
    assert events[0]["ts"]


def test_emit_trace_disabled_and_write_failure_are_silent(tmp_path, monkeypatch):
    path = tmp_path / "traces.jsonl"
    monkeypatch.setenv("TRACE_ENABLED", "false")
    monkeypatch.setenv("TRACE_PATH", str(path))

    emit_trace("hidden.event")

    assert not path.exists()

    monkeypatch.setenv("TRACE_ENABLED", "true")
    monkeypatch.setenv("TRACE_PATH", str(tmp_path))
    emit_trace("cannot.write")


def test_run_agent_traces_model_and_tool(trace_path, temp_database):
    messages = [{"role": "system", "content": "测试提示词"}]
    responses = [
        model_tool_reply("get_order", {"order_id": "ORD-1001"}),
        ChatCompletionMessage(role="assistant", content="订单已支付"),
    ]

    with patch("app.harness.runtime.request_model", side_effect=responses):
        answer = run_agent("查订单", "USER-001", messages, None, trace_id="run_test")

    assert answer == "订单已支付"
    events = read_events(trace_path)
    names = [event["event"] for event in events]
    assert names.count("model.start") == 2
    assert names.count("model.end") == 2

    tool_events = [event for event in events if event["event"] == "tool.call"]
    assert len(tool_events) == 1
    assert tool_events[0]["tool"] == "get_order"
    assert tool_events[0]["ok"] is True
    assert tool_events[0]["attempts"] == 1
    assert tool_events[0]["duration_ms"] >= 0

    raw = trace_path.read_text(encoding="utf-8")
    assert "查订单" not in raw


def test_tool_timeout_trace_records_attempts(trace_path, monkeypatch):
    monkeypatch.setattr(tool_executor, "TOOL_TIMEOUT_SECONDS", 0.01)

    def slow_get_order(**kwargs):
        time.sleep(0.1)
        return {"ok": True}

    monkeypatch.setattr(tool_executor, "get_order", slow_get_order)

    result = tool_executor.execute_tool(
        tool_call("get_order", {"order_id": "ORD-1"}),
        "USER-001",
        trace_id="run_timeout",
    )

    assert result == {"ok": False, "error": "TOOL_TIMEOUT"}
    tool_events = [
        event for event in read_events(trace_path) if event["event"] == "tool.call"
    ]
    assert tool_events[0]["error"] == "TOOL_TIMEOUT"
    assert tool_events[0]["attempts"] == 2


def test_stream_agent_traces_model_events(trace_path):
    messages = [{"role": "system", "content": "测试提示词"}]
    response = ChatCompletionMessage(role="assistant", content="你好")

    def fake_stream(client, messages, tools):
        yield "token", "你"
        yield "token", "好"
        yield "message", response

    with patch("app.harness.runtime.stream_model", fake_stream):
        events = list(
            stream_agent("你好", "USER-001", messages, None, trace_id="run_stream")
        )

    assert events == [
        {"event": "token", "data": {"text": "你"}},
        {"event": "token", "data": {"text": "好"}},
    ]
    model_end = [
        event for event in read_events(trace_path) if event["event"] == "model.end"
    ]
    assert len(model_end) == 1
    assert model_end[0]["status"] == "ok"
    assert model_end[0]["token_chars"] == 2
    assert model_end[0]["tool_call_count"] == 0


def test_send_message_traces_agent_start_and_end(trace_path, temp_database):
    context = new_conversation(user_id="USER-001")
    response = ChatCompletionMessage(role="assistant", content="你好")

    with patch("app.harness.runtime.request_model", return_value=response):
        result = send_message(context=context, message="你好", client=None)

    assert result["reply"] == "你好"
    events = read_events(trace_path)
    assert events[0]["event"] == "agent.start"
    assert events[0]["source"] == "chat"
    assert "model.start" in [event["event"] for event in events]
    assert events[-1]["event"] == "agent.end"
    assert events[-1]["status"] == "ok"
    assert events[-1]["history_saved"] is True

    raw = trace_path.read_text(encoding="utf-8")
    assert "你好" not in raw


def test_confirmation_prepared_trace_omits_credential(trace_path, monkeypatch):
    def prepared(**kwargs):
        return {
            "ok": True,
            "status": "awaiting_confirmation",
            "tool": "cancel_order",
            "confirmation_id": "cfm_secret",
            "summary": "取消订单 ORD-1",
            "expires_at": "2026-11-03T12:00:00+00:00",
        }

    monkeypatch.setattr(tool_executor, "prepare_cancel_confirmation", prepared)

    tool_executor.execute_tool(
        tool_call("cancel_order", {"order_id": "ORD-1", "reason": "不想要了"}),
        "USER-001",
        session_id="sess_test",
        thread_id="thread_test",
        trace_id="run_confirmation",
    )

    events = read_events(trace_path)
    prepared_events = [
        event for event in events if event["event"] == "confirmation.prepared"
    ]
    assert len(prepared_events) == 1
    assert prepared_events[0]["tool"] == "cancel_order"
    assert prepared_events[0]["order_id"] == "ORD-1"
    assert "cfm_secret" not in trace_path.read_text(encoding="utf-8")