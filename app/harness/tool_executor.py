"""将模型提出的工具调用转换为经过检查的本地函数调用。"""

import json
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


def execute_tool(
    tool_call,
    user_id: str,
    *,
    session_id: str | None = None,
    thread_id: str | None = None,
    now: datetime | None = None,
) -> dict:
    name = tool_call.function.name

    if name not in {
        "cancel_order",
        "list_orders",
        "get_logistics",
        "get_order", 
        "get_policy", 
        "check_refund_eligibility",
        "prepare_refund_confirmation",
        "get_refund_status",
    }:
        return {"ok": False, "error": "UNKNOWN_TOOL"}

    try:
        args = json.loads(tool_call.function.arguments)
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "error": "INVALID_ARGUMENTS"}

    if not isinstance(args, dict):
        return {"ok": False, "error": "INVALID_ARGUMENTS"}

    if name == "list_orders":
        if set(args) - {"limit", "offset"}:
            return {"ok": False, "error": "INVALID_ARGUMENTS"}
        # 分页边界由 Service 统一检查，直接调用服务也不能绕过。
        return list_orders(
            user_id=user_id,
            limit=args.get("limit", 10),
            offset=args.get("offset", 0),
        )

    if name in {"prepare_refund_confirmation", "cancel_order"}:
        if set(args) != {"order_id", "reason"}:
            return {"ok": False, "error": "INVALID_ARGUMENTS"}
        if not all(isinstance(value, str) and value.strip() for value in args.values()):
            return {"ok": False, "error": "INVALID_ARGUMENTS"}
        if not all(isinstance(value, str) and value.strip() for value in (session_id, thread_id)):
            return {"ok": False, "error": "MISSING_CONTEXT"}

        prepare = prepare_cancel_confirmation if name == "cancel_order" else prepare_refund_confirmation
        return prepare(
            user_id=user_id,
            session_id=session_id,
            thread_id=thread_id,
            order_id=args["order_id"],
            reason=args["reason"],
            now=now if now is not None else datetime.now(UTC),
        )

    
    if name in{
        "get_logistics",
        "get_order", 
        "check_refund_eligibility", 
        "get_refund_status",
    }:
        if set(args) != {"order_id"}:
            return {"ok": False, "error": "INVALID_ARGUMENTS"}

        order_id = args["order_id"]

        if not isinstance(order_id, str) or not order_id.strip():
            return {"ok": False, "error": "INVALID_ARGUMENTS"}
        
        if name == "get_order":
            return get_order(user_id=user_id, order_id=order_id)

        if name == "get_logistics":
            return get_logistics(user_id=user_id, order_id=order_id)
        
        if name == "get_refund_status":
            return get_refund_status(
                user_id=user_id,
                order_id=order_id,
            )

        return check_refund_eligibility(
            user_id=user_id,
            order_id=order_id,
            now=now if now is not None else datetime.now(UTC)
        )

    if name == "get_policy":
        if set(args) != {"topic"}:
            return {"ok": False, "error": "INVALID_ARGUMENTS"}

        topic = args["topic"]

        if not isinstance(topic, str):
            return {"ok": False, "error": "INVALID_ARGUMENTS"}

        if topic not in {"cancellation", "refund"}:
            return {"ok": False, "error": "INVALID_ARGUMENTS"}

        return get_policy(topic=topic)
