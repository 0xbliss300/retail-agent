"""将模型提出的工具调用转换为经过检查的本地函数调用。"""

import json
import queue
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from app.business.tools import (
    check_refund_eligibility,
    get_logistics,
    get_order,
    get_policy,
    get_refund_status,
    list_orders,
)
from app.harness.confirmation import prepare_cancel_confirmation, prepare_refund_confirmation
from app.harness.recovery import (
    READ_ONLY_TOOLS,
    WRITE_TOOLS,
    attempts_for,
    should_retry,
)

TOOL_TIMEOUT_SECONDS = 5.0


class _InvalidArguments(ValueError):
    """工具参数不合法，由 execute_tool 统一转换为 INVALID_ARGUMENTS。"""


def _require_text(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _InvalidArguments
    return value


def _resolve_handler(
    name: str,
    args: dict,
    user_id: str,
    session_id: str | None,
    thread_id: str | None,
    now: datetime | None,
) -> Callable[[], dict]:
    """校验参数并返回真正执行工具的零参数函数。"""
    if name == "list_orders":
        if set(args) - {"limit", "offset"}:
            raise _InvalidArguments
        # 分页边界由 Service 统一检查，直接调用服务也不能绕过。
        return lambda: list_orders(
            user_id=user_id,
            limit=args.get("limit", 10),
            offset=args.get("offset", 0),
        )

    if name in WRITE_TOOLS:
        if set(args) != {"order_id", "reason"}:
            raise _InvalidArguments
        _require_text(args["order_id"])
        _require_text(args["reason"])

        prepare = (
            prepare_cancel_confirmation
            if name == "cancel_order"
            else prepare_refund_confirmation
        )
        return lambda: prepare(
            user_id=user_id,
            session_id=session_id,
            thread_id=thread_id,
            order_id=args["order_id"],
            reason=args["reason"],
            now=now if now is not None else datetime.now(UTC),
        )

    if name in {"get_logistics", "get_order", "check_refund_eligibility", "get_refund_status"}:
        if set(args) != {"order_id"}:
            raise _InvalidArguments
        order_id = _require_text(args["order_id"])

        if name == "get_order":
            return lambda: get_order(user_id=user_id, order_id=order_id)
        if name == "get_logistics":
            return lambda: get_logistics(user_id=user_id, order_id=order_id)
        if name == "get_refund_status":
            return lambda: get_refund_status(user_id=user_id, order_id=order_id)
        return lambda: check_refund_eligibility(
            user_id=user_id,
            order_id=order_id,
            now=now if now is not None else datetime.now(UTC),
        )

    if name == "get_policy":
        if set(args) != {"topic"}:
            raise _InvalidArguments
        topic = args["topic"]
        if not isinstance(topic, str) or topic not in {"cancellation", "refund"}:
            raise _InvalidArguments
        return lambda: get_policy(topic=topic)

    raise _InvalidArguments


def _normalize_exception(exc: Exception) -> dict:
    """把内部异常转成稳定错误码，不把 SQL 或堆栈暴露给模型。"""
    if isinstance(exc, sqlite3.OperationalError):
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            return {"ok": False, "error": "TOOL_BUSY"}
    return {"ok": False, "error": "TOOL_ERROR"}


def _run_with_timeout(handler: Callable[[], dict], timeout: float) -> dict:
    """在独立线程执行工具；超时后主流程立即返回，不等待卡住的调用。"""
    outcome: queue.Queue = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            outcome.put(("result", handler()))
        except Exception as exc:  # 统一转换，避免异常冒泡到整轮对话
            outcome.put(("exception", exc))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        return {"ok": False, "error": "TOOL_TIMEOUT"}

    try:
        kind, value = outcome.get_nowait()
    except queue.Empty:
        return {"ok": False, "error": "TOOL_ERROR"}

    if kind == "exception":
        return _normalize_exception(value)
    if isinstance(value, dict):
        return value
    return {"ok": False, "error": "TOOL_ERROR"}


def _run_handler(name: str, handler: Callable[[], dict]) -> dict:
    """按工具类型执行；只有只读工具会安全重试一次。"""
    result: dict = {"ok": False, "error": "TOOL_ERROR"}
    for _ in range(attempts_for(name)):
        result = _run_with_timeout(handler, TOOL_TIMEOUT_SECONDS)
        if not should_retry(name, result):
            break
    return result


def execute_tool(
    tool_call,
    user_id: str,
    *,
    session_id: str | None = None,
    thread_id: str | None = None,
    now: datetime | None = None,
) -> dict:
    name = tool_call.function.name

    if name not in READ_ONLY_TOOLS | WRITE_TOOLS:
        return {"ok": False, "error": "UNKNOWN_TOOL"}

    try:
        args = json.loads(tool_call.function.arguments)
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "error": "INVALID_ARGUMENTS"}

    if not isinstance(args, dict):
        return {"ok": False, "error": "INVALID_ARGUMENTS"}

    try:
        handler = _resolve_handler(
            name,
            args,
            user_id,
            session_id,
            thread_id,
            now,
        )
    except _InvalidArguments:
        return {"ok": False, "error": "INVALID_ARGUMENTS"}

    if name in WRITE_TOOLS and not all(
        isinstance(value, str) and value.strip()
        for value in (session_id, thread_id)
    ):
        return {"ok": False, "error": "MISSING_CONTEXT"}

    try:
        return _run_handler(name, handler)
    except Exception:
        # 兜底：执行边界的任何意外都不能让整轮对话崩溃。
        return {"ok": False, "error": "TOOL_ERROR"}