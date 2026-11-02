"""FastAPI 入口：HTTP 演示使用服务端固定模拟身份。"""

import json
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from openai import APIError

from app.agent.model_client import create_client
from app.harness.conversation import open_conversation, send_message, stream_message
from app.schemas import ChatRequest, ChatResponse, HealthResponse

APP_VERSION = "0.1.0"
DEMO_USER_ID = "USER-001"

app = FastAPI(title="RetailOps Agent", version=APP_VERSION)


def _prepare_chat(request: ChatRequest, now: datetime) -> tuple[str, dict]:
    message = request.message.strip()
    if not message:
        raise HTTPException(
            status_code=422,
            detail={"error": "EMPTY_MESSAGE", "message": "消息不能为空。"},
        )

    if (request.session_id is None) != (request.thread_id is None):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_SESSION_CONTEXT",
                "message": "session_id 和 thread_id 必须同时提供。",
            },
        )

    try:
        context = open_conversation(
            user_id=DEMO_USER_ID,
            session_id=request.session_id,
            thread_id=request.thread_id,
            now=now,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "INVALID_SESSION_CONTEXT", "message": str(exc)},
        ) from exc

    if context is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "CONVERSATION_NOT_FOUND",
                "message": "对话不存在或已过期，请开始新对话。",
            },
        )

    return message, context


def _sse(event: dict) -> str:
    data = json.dumps(event["data"], ensure_ascii=False)
    return f"event: {event['event']}\ndata: {data}\n\n"


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        demo_user_id=DEMO_USER_ID,
        version=APP_VERSION,
    )


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    now = datetime.now(UTC)
    message, context = _prepare_chat(request, now)

    try:
        result = send_message(
            context=context,
            message=message,
            client=create_client(),
            now=now,
        )
    except APIError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "MODEL_ERROR",
                "message": "模型服务暂时不可用，请稍后重试。",
            },
        ) from exc

    return ChatResponse(**result)


@app.post("/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    now = datetime.now(UTC)
    message, context = _prepare_chat(request, now)
    client = create_client()

    def events():
        for event in stream_message(
            context=context,
            message=message,
            client=client,
            now=now,
        ):
            yield _sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )