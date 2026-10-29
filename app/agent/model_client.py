"""模型适配：创建客户端，以及发送一次请求。"""

import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


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
