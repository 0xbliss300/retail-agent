"""会话消息与模型、工具之间的执行循环。"""

import json

from app.agent.model_client import request_model
from app.business.tools import TOOLS
from app.harness.tool_executor import execute_tool



def create_messages(system_prompt: str) -> list:
    """每段新对话创建独立的消息列表。"""
    return [{"role": "system", "content": system_prompt}]


def run_agent(
    user_message: str,
    user_id: str,
    messages: list,
    client,
    max_steps: int = 4,
) -> str:
    """处理一条用户消息，并将本轮对话追加到已有历史。"""
    if max_steps < 1:
        raise ValueError("max_steps 必须至少为 1")

    messages.append({"role": "user", "content": user_message})

    for _ in range(max_steps):
        message = request_model(client, messages, TOOLS)
        messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            return message.content or "模型没有返回文本。"

        for tool_call in message.tool_calls:
            result = execute_tool(tool_call, user_id)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    answer = "已达到本次执行的步数上限，请稍后重试。"
    messages.append({"role": "assistant", "content": answer})
    return answer
