"""上下文裁剪：窗口上限、system 保留、工具消息配对与不改动入参。"""

from copy import deepcopy
from datetime import UTC, datetime

from openai.types.chat import ChatCompletionMessage

from app.harness.context import (
    MAX_CONTEXT_MESSAGES,
    TRIM_NOTICE,
    TRUNCATED_SUFFIX,
    trim_messages,
)
from app.harness.runtime import run_agent

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)


def build_history(turns: int, *, tool_content: str = '{"ok": true}') -> list[dict]:
    """每轮 4 条：user → assistant(tool_calls) → tool → assistant(text)。"""
    messages = [{"role": "system", "content": "系统提示"}]
    for index in range(turns):
        call_id = f"call_{index}"
        messages.extend(
            [
                {"role": "user", "content": f"问题{index}"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": "get_order", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": call_id, "content": tool_content},
                {"role": "assistant", "content": f"回答{index}"},
            ]
        )
    return messages


def test_short_history_is_unchanged():
    messages = build_history(2)

    window, stats = trim_messages(messages)

    assert window == messages
    assert stats == {"before": 9, "after": 9, "dropped": 0, "truncated": 0}


def test_long_history_is_trimmed_to_budget():
    messages = build_history(10)

    window, stats = trim_messages(messages, max_messages=MAX_CONTEXT_MESSAGES)

    assert stats["before"] == 41
    assert stats["dropped"] > 0
    assert len(window) <= MAX_CONTEXT_MESSAGES + 3
    assert stats["after"] == len(window)


def test_system_message_is_kept_and_notes_what_was_dropped():
    messages = build_history(10)

    window, stats = trim_messages(messages)

    assert window[0]["role"] == "system"
    assert "系统提示" in window[0]["content"]
    assert TRIM_NOTICE.format(count=stats["dropped"]) in window[0]["content"]
    assert [m for m in window if m["role"] == "system"] == [window[0]]


def test_trimmed_window_has_no_orphan_tool_message():
    messages = build_history(10)

    for max_messages in range(6, 30):
        window, _ = trim_messages(messages, max_messages=max_messages)
        assert window[0]["role"] == "system"
        assert window[1]["role"] != "tool", max_messages

        declared = {
            call["id"]
            for message in window
            for call in (message.get("tool_calls") or [])
        }
        for message in window:
            if message["role"] == "tool":
                assert message["tool_call_id"] in declared


def test_recent_messages_are_preserved():
    messages = build_history(10)

    window, _ = trim_messages(messages)

    assert window[-1] == messages[-1]
    assert window[-2] == messages[-2]


def test_long_tool_results_are_truncated():
    messages = build_history(3, tool_content="x" * 5000)

    window, stats = trim_messages(messages, max_tool_chars=100)

    tools = [m for m in window if m["role"] == "tool"]
    assert tools
    assert all(len(m["content"]) == 100 + len(TRUNCATED_SUFFIX) for m in tools)
    assert all(m["content"].endswith(TRUNCATED_SUFFIX) for m in tools)
    assert stats["truncated"] == 3


def test_input_messages_are_not_modified():
    messages = build_history(10, tool_content="y" * 5000)
    snapshot = deepcopy(messages)

    trim_messages(messages, max_messages=12, max_tool_chars=50)

    assert messages == snapshot


def test_runtime_sends_trimmed_window(monkeypatch):
    captured = {}

    def fake_request_model(client, messages, tools):
        captured["messages"] = list(messages)
        return ChatCompletionMessage(role="assistant", content="好的")

    monkeypatch.setattr("app.harness.runtime.request_model", fake_request_model)
    messages = build_history(10)
    history_length = len(messages)

    answer = run_agent("继续", "USER-1", messages, None, now=NOW)

    assert answer == "好的"
    sent = captured["messages"]
    assert len(sent) <= MAX_CONTEXT_MESSAGES + 3
    assert sent[0]["role"] == "system"
    assert sent[-1]["content"] == "继续"
    # 持久化用的完整历史没有被裁剪，只是追加了本轮用户消息和回复。
    assert len(messages) == history_length + 2
