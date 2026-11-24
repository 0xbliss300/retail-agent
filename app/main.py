"""FastAPI 入口。"""

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import APIError

from app.agent.model_client import create_client
from app.auth import AuthenticatedUser
from app.auth import router as auth_router
from app.business import service as business
from app.business.policies import POLICIES
from app.business.service import approve_withdraw_confirmation
from app.harness.confirmation import prepare_withdraw_confirmation
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
    ConfirmResponse,
    HealthResponse,
    RecoverRequest,
    RecoverResponse,
)

APP_VERSION = "0.1.0"

app = FastAPI(title="RetailOps Agent", version=APP_VERSION)
app.include_router(auth_router)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    if request.url.path == "/auth/login":
        return JSONResponse(
            status_code=422,
            content={"detail": {"error": "INVALID_CREDENTIALS", "message": "请输入有效的邮箱和密码。"}},
        )
    return await request_validation_exception_handler(request, exc)


def _prepare_chat(request: ChatRequest, user_id: str, now: datetime) -> tuple[str, dict]:
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
            user_id=user_id,
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
        version=APP_VERSION,
    )


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, user_id: AuthenticatedUser) -> ChatResponse:
    now = datetime.now(UTC)
    message, context = _prepare_chat(request, user_id, now)

    try:
        result = send_message(
            context=context,
            message=message,
            client=create_client(),
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


@app.post("/confirm", response_model=ConfirmResponse)
def confirm(request: ConfirmRequest, user_id: AuthenticatedUser) -> ConfirmResponse:
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
    scope = {"user_id": user_id, "session_id": session_id, "thread_id": thread_id}
    try:
        context = open_conversation(**scope, now=now)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "INVALID_SESSION_CONTEXT", "message": str(exc)},
        ) from exc

    if context is None and request.decision == "approve":
        record = business.get_confirmation(**scope, confirmation_id=confirmation_id)
        if record and record["tool"] == "cancel_order" and record["status"] == "consumed":
            # 已执行的取消结果独立于聊天历史的有效期。
            context = scope

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

    return ConfirmResponse(**result)


@app.post("/recover", response_model=RecoverResponse)
def recover(request: RecoverRequest, user_id: AuthenticatedUser) -> RecoverResponse:
    """断线后查询操作的真实结果；不调用模型、不写数据库。"""
    result = describe_recovery(
        user_id=user_id,
        session_id=request.session_id.strip(),
        thread_id=request.thread_id.strip(),
        confirmation_id=request.confirmation_id.strip() if request.confirmation_id is not None else None,
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
def chat_stream(request: ChatRequest, user_id: AuthenticatedUser) -> StreamingResponse:
    now = datetime.now(UTC)
    message, context = _prepare_chat(request, user_id, now)
    client = create_client()

    def events():
        for event in stream_message(
            context=context,
            message=message,
            client=client,
        ):
            yield _sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# 前端只读接口：订单、售后申请与政策。
# 订单和售后接口与 /chat 共享同一个登录身份。
# ---------------------------------------------------------------------------

# 页面上的撤回操作使用独立的会话上下文：用户在对话框中的点击就是确认动作，
# 服务端仍按“准备确认 → 写事务内复查并执行”的既有流程处理。
UI_WITHDRAW_SESSION = "ui:refund-withdraw"


def _policy_items(text: str) -> list[str]:
    return [f"{sentence.strip()}。" for sentence in text.split("。") if sentence.strip()]


def _require_ok(result: dict, status_code: int, message: str) -> dict:
    if not result.get("ok"):
        raise HTTPException(
            status_code=status_code,
            detail={"error": result.get("error", "INVALID_ARGUMENTS"), "message": message},
        )
    return result


@app.get("/orders")
def list_orders(
    user_id: AuthenticatedUser,
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
) -> dict:
    result = business.list_orders(user_id=user_id, limit=limit, offset=offset)
    return _require_ok(result, 422, "分页参数无效。")


@app.get("/orders/{order_id}")
def order_detail(order_id: str, user_id: AuthenticatedUser) -> dict:
    order_result = business.get_order(user_id=user_id, order_id=order_id.strip())
    if not order_result.get("ok"):
        raise HTTPException(
            status_code=404,
            detail={"error": "ORDER_NOT_FOUND", "message": "未找到该订单。"},
        )
    order = {key: value for key, value in order_result["order"].items() if key != "user_id"}
    logistics = business.get_logistics(user_id=user_id, order_id=order["order_id"])
    eligibility = business.check_refund_eligibility(
        user_id=user_id,
        order_id=order["order_id"],
        now=datetime.now(UTC),
    )
    latest_refund = business.get_refund_status(
        user_id=user_id,
        order_id=order["order_id"],
    )
    return {
        "order": order,
        "logistics": logistics.get("logistics"),
        "refund_eligibility": eligibility,
        "latest_refund": latest_refund.get("refund"),
    }


@app.get("/refunds")
def list_refunds(
    user_id: AuthenticatedUser,
    status: str | None = None,
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
) -> dict:
    result = business.list_refunds(
        user_id=user_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return _require_ok(result, 422, "筛选条件或分页参数无效。")


@app.get("/refunds/{refund_id}")
def refund_detail(refund_id: str, user_id: AuthenticatedUser) -> dict:
    result = business.get_refund(user_id=user_id, refund_id=refund_id.strip())
    if not result.get("ok"):
        raise HTTPException(
            status_code=404,
            detail={"error": "REFUND_NOT_FOUND", "message": "未找到该退款申请。"},
        )
    return result


@app.post("/refunds/{refund_id}/withdraw")
def withdraw_refund(refund_id: str, user_id: AuthenticatedUser) -> dict:
    """撤回待处理申请；页面确认后调用，服务端重新校验状态与金额。"""
    now = datetime.now(UTC)
    result = business.get_refund(user_id=user_id, refund_id=refund_id.strip())
    if not result.get("ok"):
        raise HTTPException(
            status_code=404,
            detail={"error": "REFUND_NOT_FOUND", "message": "未找到该退款申请。"},
        )

    refund = result["refund"]
    session_id = f"{UI_WITHDRAW_SESSION}:{refund['order_id']}"
    thread_id = f"{UI_WITHDRAW_SESSION}:{refund['refund_id']}"

    prepared = prepare_withdraw_confirmation(
        user_id=user_id,
        session_id=session_id,
        thread_id=thread_id,
        order_id=refund["order_id"],
        now=now,
    )
    if not prepared.get("ok"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": prepared.get("error", "WITHDRAW_REJECTED"),
                "message": prepared.get("reason", "当前申请不能撤回。"),
            },
        )

    executed = approve_withdraw_confirmation(
        user_id=user_id,
        session_id=session_id,
        thread_id=thread_id,
        confirmation_id=prepared["confirmation_id"],
        now=now,
    )
    if not executed.get("ok"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": executed.get("error", "WITHDRAW_REJECTED"),
                "message": "撤回未执行，请刷新后重试。",
            },
        )

    return {
        "status": executed["status"],
        "refund_id": executed["refund_id"],
        "order_id": executed["order_id"],
        "amount_cents": executed["amount_cents"],
    }


@app.get("/policies")
def list_policies() -> dict:
    return {
        "policies": {
            topic: _policy_items(text)
            for topic, text in POLICIES.items()
        }
    }


# ---------------------------------------------------------------------------
# 单服务托管：`cd frontend && npm run build` 之后由本服务直接提供页面。
# 未构建时保持接口服务的原始行为，不会凭空生成路由。
# ---------------------------------------------------------------------------

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"

if FRONTEND_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS), name="frontend-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def serve_frontend(path: str) -> FileResponse:
        """前端路由回落到 index.html；真实存在的静态文件直接返回。"""
        if path:
            candidate = (FRONTEND_DIST / path).resolve()
            if FRONTEND_DIST in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
