"""模型适配：创建客户端，以及发送一次请求。"""

import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from openai.types.chat import ChatCompletionMessage


def create_client() -> OpenAI:
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env")
    return OpenAI(
        api_key=os.environ["LLM_API_KEY"],
        base_url=os.environ["LLM_BASE_URL"],
        timeout=30.0,
        max_retries=2,
    )


def request_model(client: OpenAI, messages: list, tools: list):
    """只请求一次模型；是否调用工具由运行时处理。"""
    response = client.chat.completions.create(
        model=os.environ["LLM_MODEL"],
        messages=messages,
        tools=tools,
    )
    return response.choices[0].message


def stream_model(client: OpenAI, messages: list, tools: list):
    """流式请求模型：逐段产出文本，最后产出完整消息。"""
    response = client.chat.completions.create(
        model=os.environ["LLM_MODEL"],
        messages=messages,
        tools=tools,
        stream=True,
    )

    content_parts: list[str] = []
    tool_calls: dict[int, dict] = {}

    for chunk in response:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta is None:
            continue

        if delta.content:
            content_parts.append(delta.content)
            yield "token", delta.content

        for call in delta.tool_calls or []:
            index = call.index if call.index is not None else 0
            entry = tool_calls.setdefault(
                index,
                {
                    "id": None,
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )
            if call.id:
                entry["id"] = call.id
            if call.type:
                entry["type"] = call.type
            if call.function is not None:
                if call.function.name:
                    entry["function"]["name"] += call.function.name
                if call.function.arguments:
                    entry["function"]["arguments"] += call.function.arguments

    calls = []
    for index in sorted(tool_calls):
        entry = tool_calls[index]
        entry["id"] = entry["id"] or f"call_{index}"
        calls.append(entry)

    message = ChatCompletionMessage(
        role="assistant",
        content="".join(content_parts) or None,
        tool_calls=calls or None,
    )
    yield "message", message
