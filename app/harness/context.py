"""发给模型前的上下文裁剪。

持久化仍保存完整历史；这里只决定「哪些消息进入本次模型调用」。
裁剪必须保持 ``assistant(tool_calls)`` 与 ``tool`` 消息成对，
否则 OpenAI 兼容接口会直接返回 400，而不是降级。
"""

from typing import Any

# 一次模型调用最多带多少条消息（含系统提示）。
MAX_CONTEXT_MESSAGES = 24
# 单条工具结果的最大字符数；超出部分截断并标记。
MAX_TOOL_RESULT_CHARS = 1200
TRIM_NOTICE = "更早的 {count} 条对话记录已省略；业务结果以工具返回和数据库为准。"
TRUNCATED_SUFFIX = "…[内容已截断]"


def _truncate_tool_results(
    messages: list[dict[str, Any]],
    max_chars: int,
) -> tuple[list[dict[str, Any]], int]:
    """复制消息并截断过长的工具结果，不修改入参。"""
    result: list[dict[str, Any]] = []
    truncated = 0
    for message in messages:
        content = message.get("content")
        if (
            message.get("role") == "tool"
            and isinstance(content, str)
            and len(content) > max_chars
        ):
            message = {**message, "content": content[:max_chars] + TRUNCATED_SUFFIX}
            truncated += 1
        result.append(message)
    return result, truncated


def _align_start(body: list[dict[str, Any]], start: int) -> int:
    """把窗口起点前移，避免窗口以孤立的 tool 消息开始。"""
    while start > 0 and body[start].get("role") == "tool":
        start -= 1
    return start


def trim_messages(
    messages: list[dict[str, Any]],
    *,
    max_messages: int = MAX_CONTEXT_MESSAGES,
    max_tool_chars: int = MAX_TOOL_RESULT_CHARS,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """返回 (发给模型的消息, 统计)。始终保留开头的 system 消息。

    统计字段：before、after、dropped、truncated。
    """
    if not isinstance(messages, list):
        raise ValueError("messages 必须是列表")
    if max_messages < 2:
        raise ValueError("max_messages 至少为 2")

    trimmed, truncated = _truncate_tool_results(messages, max_tool_chars)
    before = len(trimmed)

    head = 0
    while head < len(trimmed) and trimmed[head].get("role") == "system":
        head += 1
    system_messages = trimmed[:head]
    body = trimmed[head:]

    budget = max(1, max_messages - len(system_messages))
    start = max(0, len(body) - budget)
    start = _align_start(body, start)
    kept = body[start:]
    dropped = start

    if dropped:
        notice = TRIM_NOTICE.format(count=dropped)
        if system_messages:
            last = system_messages[-1]
            system_messages = system_messages[:-1] + [
                {
                    **last,
                    "content": f"{last.get('content') or ''}\n\n{notice}".strip(),
                }
            ]
        else:
            system_messages = [{"role": "system", "content": notice}]

    window = system_messages + kept
    return window, {
        "before": before,
        "after": len(window),
        "dropped": dropped,
        "truncated": truncated,
    }
