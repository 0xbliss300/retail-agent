"""将模型提出的工具调用转换为经过检查的本地函数调用。"""

import json
from datetime import datetime, timezone

from app.business.tools import (
    check_refund_eligibility,
    get_order,
    get_policy,
)
from app.harness.confirmation import prepare_refund_confirmation


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
        "get_order", 
        "get_policy", 
        "check_refund_eligibility",
        "prepare_refund_confirmation",
    }:
        return {"ok": False, "error": "UNKNOWN_TOOL"}

    try:
        args = json.loads(tool_call.function.arguments)
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "error": "INVALID_ARGUMENTS"}

    if not isinstance(args, dict):
        return {"ok": False, "error": "INVALID_ARGUMENTS"}

    if name == "prepare_refund_confirmation":
        if set(args) != {"order_id", "reason"}:
            return {"ok": False, "error": "INVALID_ARGUMENTS"}
        if not all(isinstance(value, str) and value.strip() for value in args.values()):
            return {"ok": False, "error": "INVALID_ARGUMENTS"}
        if not all(isinstance(value, str) and value.strip() for value in (session_id, thread_id)):
            return {"ok": False, "error": "MISSING_CONTEXT"}

        return prepare_refund_confirmation(
            user_id=user_id,
            session_id=session_id,
            thread_id=thread_id,
            order_id=args["order_id"],
            reason=args["reason"],
            now=now if now is not None else datetime.now(timezone.utc),
        )

    
    if name in{"get_order", "check_refund_eligibility"}:
        if set(args) != {"order_id"}:
            return {"ok": False, "error": "INVALID_ARGUMENTS"}

        order_id = args["order_id"]

        if not isinstance(order_id, str) or not order_id.strip():
            return {"ok": False, "error": "INVALID_ARGUMENTS"}
        if name == "get_order":
            return get_order(user_id=user_id, order_id=order_id)

        return check_refund_eligibility(
            user_id=user_id,
            order_id=order_id,
            now=now if now is not None else datetime.now(timezone.utc)
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
