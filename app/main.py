"""FastAPI 入口：HTTP 演示使用服务端固定模拟身份。"""

import json
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from openai import APIError

from app.agent.model_client import create_client
from app.harness.conversation import (
    decide_confirmation,
    open_conversation,
    send_message,
    stream_message,
)
from app.harness.recovery import describe_recovery
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ConfirmRequest,
    HealthResponse,
    RecoverRequest,
    RecoverResponse,
)

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


@app.post("/confirm", response_model=ChatResponse)
def confirm(request: ConfirmRequest) -> ChatResponse:
    session_id = request.session_id.strip()
    thread_id = request.thread_id.strip()
    confirmation_id = request.confirmation_id.strip()
    if not all((session_id, thread_id, confirmation_id)):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "INVALID_ARGUMENTS",
                "message": "会话编号和确认编号不能为空。",
            },
        )

    now = datetime.now(UTC)
    try:
        context = open_conversation(
            user_id=DEMO_USER_ID,
            session_id=session_id,
            thread_id=thread_id,
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
                "message": "对话不存在或已过期，请重新发起操作。",
            },
        )

    result = decide_confirmation(
        context=context,
        confirmation_id=confirmation_id,
        decision=request.decision,
        now=now,
    )
    error = result.get("error")
    if error == "CONFIRMATION_NOT_FOUND":
        raise HTTPException(
            status_code=404,
            detail={"error": error, "message": "未找到当前会话中的待确认操作。"},
        )
    if error == "CONFIRMATION_INVALID":
        raise HTTPException(
            status_code=409,
            detail={"error": error, "message": "确认记录无效。"},
        )
    if error == "INVALID_ARGUMENTS":
        raise HTTPException(
            status_code=422,
            detail={"error": error, "message": "确认参数无效。"},
        )

    return ChatResponse(**result)


@app.post("/recover", response_model=RecoverResponse)
def recover(request: RecoverRequest) -> RecoverResponse:
    """断线后查询上一次高风险操作的真实结果；不调用模型、不写数据库。"""
    result = describe_recovery(
        user_id=DEMO_USER_ID,
        session_id=request.session_id.strip(),
        thread_id=request.thread_id.strip(),
        now=datetime.now(UTC),
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=422,
            detail={
                "error": result.get("error", "INVALID_ARGUMENTS"),
                "message": "会话上下文不完整，无法查询。",
            },
        )
    payload = {key: value for key, value in result.items() if key != "ok"}
    return RecoverResponse(**payload)


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