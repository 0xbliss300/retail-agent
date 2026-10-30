"""组合数据访问与业务规则，供工具或服务端入口调用。"""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from app.business.policies import POLICIES, check_refund_window
from app.business.repository import (
    close_pending_confirmation,
    consume_refund_confirmation,
    get_active_refund,
    get_confirmation_for_context,
    get_order_for_user,
    insert_refund_request,
)
from app.storage.database import get_connection


def get_order(*, user_id: str, order_id: str) -> dict:
    """查询可访问订单；不存在与无权限返回相同结果。"""
    order = get_order_for_user(user_id=user_id, order_id=order_id)
    if order is None:
        return {"ok": False, "error": "ORDER_NOT_FOUND"}

    return {"ok": True, "order": order}


def get_policy(*, topic: str) -> dict:
    """读取通用政策；不替代具体订单的资格判断。"""
    policy = POLICIES.get(topic)
    if policy is None:
        return {"ok": False, "error": "POLICY_NOT_FOUND"}

    return {"ok": True, "topic": topic, "policy": policy}


def check_refund_eligibility(
    *,
    user_id: str,
    order_id: str,
    now: datetime,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """只读资格预检；实际创建申请时仍须在写事务中重新校验。"""
    order = get_order_for_user(
        user_id=user_id, 
        order_id=order_id, 
        conn=conn
    )
    if order is None:
        return {"ok": False, "error": "ORDER_NOT_FOUND"}

    window = check_refund_window(
        status=order["status"],
        shipped_at=order["shipped_at"],
        delivered_at=order["delivered_at"],
        now=now,
    )
    if not window["window_open"]:
        return {
            "ok": True,
            "eligible": False,
            "policy_code": window["code"],
            "reason": window["reason"],
        }

    active_refund = get_active_refund(
        user_id=user_id, 
        order_id=order_id, 
        conn=conn
    )
    if active_refund is not None:
        return {
            "ok": True,
            "eligible": False,
            "policy_code": "ACTIVE_REFUND_EXISTS",
            "reason": "该订单已有处理中的售后退款申请，不可重复申请。",
        }

    return {
        "ok": True,
        "eligible": True,
        "policy_code": "ELIGIBLE",
        "reason": "当前符合退款申请条件，提交申请仍需用户确认。",
        "amount_cents": order["paid_amount_cents"],
    }

# 批准refund操作
def approve_refund_confirmation(
    *,
    user_id: str,
    session_id: str,
    thread_id: str,
    confirmation_id: str,
    now: datetime,
) -> dict:
    """供可信入口在用户明确确认后调用；不直接开放给模型。"""
    context = {
        "user_id": user_id,
        "session_id": session_id,
        "thread_id": thread_id,
        "confirmation_id": confirmation_id,
    }
    if not all(
        isinstance(value, str) and value.strip()
        for value in context.values()
    ):
        return {"ok": False, "error": "INVALID_ARGUMENTS"}

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须包含时区")
    now = now.astimezone(timezone.utc)

    conn = get_connection()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            record = get_confirmation_for_context(conn, **context)

            if record is None:
                return {"ok": False, "error": "CONFIRMATION_NOT_FOUND"}

            if record["tool"] != "create_refund":
                return {"ok": False, "error": "CONFIRMATION_INVALID"}

            # 已成功执行：复用已保存结果，不再创建申请。
            if record["status"] == "consumed":
                return json.loads(record["result_json"])

            if record["status"] == "expired":
                return {"ok": False, "error": "CONFIRMATION_EXPIRED"}

            if record["status"] != "pending":
                return {"ok": False, "error": "CONFIRMATION_NOT_PENDING"}

            expires_at = datetime.fromisoformat(record["expires_at"])
            if expires_at.tzinfo is None or expires_at.utcoffset() is None:
                raise ValueError("确认记录的过期时间必须包含时区")

            if now >= expires_at:
                close_pending_confirmation(
                    conn, **context, status="expired",
                )
                return {"ok": False, "error": "CONFIRMATION_EXPIRED"}

            # 使用准备阶段保存的参数，不接收新的订单、原因或金额。
            try:
                params = json.loads(record["params_json"])
            except (TypeError, ValueError):
                return {"ok": False, "error": "CONFIRMATION_INVALID"}

            fingerprint = hashlib.sha256(
                record["params_json"].encode("utf-8")
            ).hexdigest()

            if (
                fingerprint != record["params_fingerprint"]
                or not isinstance(params, dict)
                or set(params) != {"order_id", "reason", "amount_cents"}
                or params["order_id"] != record["order_id"]
                or not isinstance(params["reason"], str)
                or not params["reason"].strip()
                or type(params["amount_cents"]) is not int
                or params["amount_cents"] < 0
                or record["idempotency_key"] != f"confirm:{confirmation_id}"
            ):
                return {"ok": False, "error": "CONFIRMATION_INVALID"}

            # 必须复查，并且使用当前写事务的连接。
            eligibility = check_refund_eligibility(
                user_id=user_id,
                order_id=record["order_id"],
                now=now,
                conn=conn,
            )
            if not eligibility["ok"]:
                return eligibility

            if not eligibility["eligible"]:
                return {
                    "ok": False,
                    "error": "POLICY_DENIED",
                    "policy_code": eligibility["policy_code"],
                    "reason": eligibility["reason"],
                }

            # 金额发生变化时，不能按旧摘要直接提交。
            if eligibility["amount_cents"] != params["amount_cents"]:
                return {"ok": False, "error": "CONFIRMATION_STALE"}

            timestamp = now.isoformat()
            refund_id = f"rf_{uuid4().hex}"
            result = {
                "ok": True,
                "status": "submitted",
                "refund_id": refund_id,
                "order_id": record["order_id"],
                "amount_cents": eligibility["amount_cents"],
                "refund_status": "pending",
            }

            insert_refund_request(
                conn,
                record={
                    "refund_id": refund_id,
                    "order_id": record["order_id"],
                    "user_id": user_id,
                    "amount_cents": eligibility["amount_cents"],
                    "reason": params["reason"],
                    "idempotency_key": record["idempotency_key"],
                    "confirmation_id": confirmation_id,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
            )
            consume_refund_confirmation(
                conn,
                context=context,
                result_json=json.dumps(result, ensure_ascii=False),
                timestamp=timestamp,
            )

        # 到这里，申请与确认结果已经一起提交。
        return result
    finally:
        conn.close()