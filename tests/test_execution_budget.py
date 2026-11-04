"""执行预算：工具调用配额、耗时上限、消息配对与流式行为。"""

import json
from datetime import UTC, datetime

import pytest
from openai.types.chat import ChatCompletionMessage

from app.harness.budget import (
    TIME_BUDGET_ANSWER,
    TOOL_BUDGET_ANSWER,
    ExecutionBudget,
)
from app.harness.runtime import create_messages, run_agent, stream_agent
from app.storage import database

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


@pytest.fixture
def order_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO orders (order_id, user_id, status, paid_amount_cents)
                VALUES ('ORD-B1', 'USER-B', 'paid', 25900)
                """
            )
    finally:
        conn.close()


def multi_call(count: int) -> ChatCompletionMessage:
    return ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[
            {
                "id": f"call_{index}",
                "type": "function",
                "function": {
                    "name": "get_order",
                    "arguments": json.dumps({"order_id": "ORD-B1"}),
                },
            }
            for index in range(count)
        ],
    )


def install_model(monkeypatch, message: ChatCompletionMessage) -> None:
    monkeypatch.setattr(
        "app.harness.runtime.request_model",
        lambda client, messages, tools: message,
    )


def test_budget_rejects_invalid_limits():
    with pytest.raises(ValueError):
        ExecutionBudget(max_tool_calls=0)
    with pytest.raises(ValueError):
        ExecutionBudget(max_seconds=0)


def test_tool_quota_stops_extra_calls(order_database, monkeypatch):
    install_model(monkeypatch, multi_call(10))
    messages = create_messages("测试")

    answer = run_agent(
        "查十个订单", "USER-B", messages, None, now=NOW,
        budget=ExecutionBudget(max_tool_calls=3),
    )

    assert answer == TOOL_BUDGET_ANSWER
    tool_messages = [m for m in messages if m["role"] == "tool"]
    assert len(tool_messages) == 10
    results = [json.loads(m["content"]) for m in tool_messages]
    assert sum(1 for r in results if r.get("error") == "BUDGET_EXCEEDED") == 7


def test_tool_quota_keeps_tool_call_pairing(order_database, monkeypatch):
    """超预算也必须给每个 tool_call 补结果，否则下一轮请求不合法。"""
    install_model(monkeypatch, multi_call(5))
    messages = create_messages("测试")

    run_agent(
        "查五个订单", "USER-B", messages, None, now=NOW,
        budget=ExecutionBudget(max_tool_calls=2),
    )

    assistant = next(m for m in messages if m.get("tool_calls"))
    declared = {call["id"] for call in assistant["tool_calls"]}
    answered = {m["tool_call_id"] for m in messages if m["role"] == "tool"}
    assert declared == answered


def test_calls_within_budget_are_all_executed(order_database, monkeypatch):
    install_model(monkeypatch, multi_call(3))
    messages = create_messages("测试")

    answer = run_agent(
        "查三个订单", "USER-B", messages, None, max_steps=1, now=NOW,
        budget=ExecutionBudget(max_tool_calls=3),
    )

    assert "步数上限" in answer
    results = [
        json.loads(m["content"]) for m in messages if m["role"] == "tool"
    ]
    assert len(results) == 3
    assert all(r.get("error") != "BUDGET_EXCEEDED" for r in results)


def test_elapsed_budget_skips_model_call(order_database, monkeypatch):
    clock = FakeClock()
    budget = ExecutionBudget(max_seconds=5, clock=clock)
    clock.value = 6

    def fail_request_model(client, messages, tools):
        raise AssertionError("超时后不应再调用模型")

    monkeypatch.setattr(
        "app.harness.runtime.request_model", fail_request_model,
    )
    messages = create_messages("测试")

    answer = run_agent("你好", "USER-B", messages, None, now=NOW, budget=budget)

    assert answer == TIME_BUDGET_ANSWER
    assert messages[-1] == {"role": "assistant", "content": TIME_BUDGET_ANSWER}


def test_budget_exceeded_emits_trace(order_database, monkeypatch, tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("TRACE_ENABLED", "true")
    monkeypatch.setenv("TRACE_PATH", str(trace_path))
    install_model(monkeypatch, multi_call(4))

    run_agent(
        "查订单", "USER-B", create_messages("测试"), None, now=NOW,
        budget=ExecutionBudget(max_tool_calls=1),
    )

    events = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    exceeded = [e for e in events if e["event"] == "budget.exceeded"]
    assert exceeded
    assert exceeded[0]["kind"] == "tool_calls"
    assert exceeded[0]["tool_calls"] == 1
    assert exceeded[0]["max_tool_calls"] == 1


def test_stream_stops_at_tool_quota(order_database, monkeypatch):
    def fake_stream_model(client, messages, tools):
        yield "message", multi_call(4)

    monkeypatch.setattr("app.harness.runtime.stream_model", fake_stream_model)
    messages = create_messages("测试")

    events = list(
        stream_agent(
            "查订单", "USER-B", messages, None, now=NOW,
            budget=ExecutionBudget(max_tool_calls=1),
        )
    )

    assert events[-1]["data"]["text"] == TOOL_BUDGET_ANSWER
    results = [
        json.loads(m["content"]) for m in messages if m["role"] == "tool"
    ]
    assert sum(1 for r in results if r.get("error") == "BUDGET_EXCEEDED") == 3


def test_stream_elapsed_budget_skips_model_call(order_database, monkeypatch):
    clock = FakeClock()
    budget = ExecutionBudget(max_seconds=1, clock=clock)
    clock.value = 2

    def fail_stream_model(client, messages, tools):
        raise AssertionError("超时后不应再调用模型")
        yield  # pragma: no cover

    monkeypatch.setattr("app.harness.runtime.stream_model", fail_stream_model)

    events = list(
        stream_agent("你好", "USER-B", create_messages("测试"), None, now=NOW, budget=budget)
    )

    assert events == [{"event": "token", "data": {"text": TIME_BUDGET_ANSWER}}]
