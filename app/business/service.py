"""组合数据访问与业务规则，供工具或服务端入口调用。"""
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from app.business import cancellation
from app.business.policies import POLICIES, check_refund_window
from app.business.repository import (
    close_pending_confirmation,
    complete_refund_in_transaction,
    consume_refund_confirmation,
    consume_withdraw_confirmation,
    get_active_refund,
    get_after_sales_refund_for_withdraw,
    get_confirmation_for_context,
    get_latest_after_sales_refund,
    get_logistics_for_user,
    get_order_for_user,
    get_orders_for_user,
    get_refund_for_settlement,
    get_refund_for_user,
    get_refund_settlement,
    insert_refund_request,
    insert_refund_settlement,
    list_refunds_for_user,
    refund_order_in_transaction,
    withdraw_refund_in_transaction,
)
from app.storage.database import get_connection


def check_cancel_eligibility(
    *, user_id: str, order_id: str, now: datetime, conn: sqlite3.Connection | None = None,
) -> dict:
    return cancellation.check_cancel_eligibility(user_id=user_id, order_id=order_id, now=now, conn=conn)


def approve_cancel_confirmation(
    *, user_id: str, session_id: str, thread_id: str, confirmation_id: str, now: datetime,
) -> dict:
    return cancellation.approve_cancel_confirmation(
        user_id=user_id, session_id=session_id, thread_id=thread_id, confirmation_id=confirmation_id, now=now,
    )


def get_order(*, user_id: str, order_id: str) -> dict:
    """查询可访问订单；不存在与无权限返回相同结果。"""
    order = get_order_for_user(user_id=user_id, order_id=order_id)
    if order is None:
        return {"ok": False, "error": "ORDER_NOT_FOUND"}

    return {"ok": True, "order": order}


def list_orders(*, user_id: str, limit: int = 10, offset: int = 0) -> dict:
    """分页查询本人订单；多读一条判断是否还有下一页。"""
    if not isinstance(user_id, str) or not user_id.strip():
        return {"ok": False, "error": "MISSING_CONTEXT"}
    if (
        type(limit) is not int or not 1 <= limit <= 50
        or type(offset) is not int or not 0 <= offset <= 2**63 - 1
    ):
        return {"ok": False, "error": "INVALID_ARGUMENTS"}
    rows = get_orders_for_user(user_id=user_id, limit=limit + 1, offset=offset)
    has_more = len(rows) > limit
    return {
        "ok": True,
        "orders": rows[:limit],
        "limit": limit,
        "offset": offset,
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
    }


REFUND_STATUSES = frozenset({
    "pending", "approved", "rejected", "completed", "failed", "withdrawn",
})


def list_refunds(
    *,
    user_id: str,
    status: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict:
    """分页查询本人售后申请；多读一条判断是否还有下一页。"""
    if not isinstance(user_id, str) or not user_id.strip():
        return {"ok": False, "error": "MISSING_CONTEXT"}
    if status is not None and (
        not isinstance(status, str) or status not in REFUND_STATUSES
    ):
        return {"ok": False, "error": "INVALID_ARGUMENTS"}
    if (
        type(limit) is not int or not 1 <= limit <= 50
        or type(offset) is not int or not 0 <= offset <= 2**63 - 1
    ):
        return {"ok": False, "error": "INVALID_ARGUMENTS"}

    rows = list_refunds_for_user(
        user_id=user_id,
        status=status,
        limit=limit + 1,
        offset=offset,
    )
    has_more = len(rows) > limit
    return {
        "ok": True,
        "refunds": rows[:limit],
        "status": status,
        "limit": limit,
        "offset": offset,
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
    }


def get_refund(*, user_id: str, refund_id: str) -> dict:
    """读取单条售后申请及其订单；不返回内部身份字段。"""
    if not isinstance(refund_id, str) or not refund_id.strip():
        return {"ok": False, "error": "INVALID_ARGUMENTS"}
    refund = get_refund_for_user(user_id=user_id, refund_id=refund_id.strip())
    if refund is None:
        return {"ok": False, "error": "REFUND_NOT_FOUND"}

    return {
        "ok": True,
        "refund": {key: value for key, value in refund.items() if key != "user_id"},
    }


def get_logistics(*, user_id: str, order_id: str) -> dict:
    """订单归属通过后查询物流；暂无记录与订单不可访问分别处理。"""
    order = get_order_for_user(user_id=user_id, order_id=order_id)
    if order is None:
        return {"ok": False, "error": "ORDER_NOT_FOUND"}
    return {
        "ok": True,
        "order_id": order_id,
        "logistics": get_logistics_for_user(user_id=user_id, order_id=order_id),
    }


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

def get_confirmation(
    *,
    user_id: str,
    session_id: str,
    thread_id: str,
    confirmation_id: str,
) -> dict | None:
    """读取当前上下文中的确认记录，不改变状态；用于重复确认时重放结果。"""
    values = (user_id, session_id, thread_id, confirmation_id)
    if not all(isinstance(value, str) and value.strip() for value in values):
        return None

    conn = get_connection()
    try:
        with conn:
            conn.execute("BEGIN")
            return get_confirmation_for_context(
                conn,
                user_id=user_id,
                session_id=session_id,
                thread_id=thread_id,
                confirmation_id=confirmation_id,
            )
    finally:
        conn.close()


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
    now = now.astimezone(UTC)

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


def _withdraw_params(record: dict) -> dict | None:
    """校验撤回确认单保存的参数；不接收模型新传入的值。"""
    raw = record["params_json"]
    if not isinstance(raw, str):
        return None
    try:
        params = json.loads(raw)
    except ValueError:
        return None
    if (
        hashlib.sha256(raw.encode("utf-8")).hexdigest() != record["params_fingerprint"]
        or not isinstance(params, dict)
        or set(params) != {"order_id", "refund_id", "amount_cents"}
        or params["order_id"] != record["order_id"]
        or not isinstance(params["refund_id"], str)
        or not params["refund_id"].strip()
        or type(params["amount_cents"]) is not int
        or params["amount_cents"] < 0
        or record["idempotency_key"] != f"confirm:{record['confirmation_id']}"
    ):
        return None
    return params


def approve_withdraw_confirmation(
    *,
    user_id: str,
    session_id: str,
    thread_id: str,
    confirmation_id: str,
    now: datetime,
) -> dict:
    """供可信入口在用户确认后撤回尚未处理的售后申请；不开放给模型。"""
    context = {
        "user_id": user_id,
        "session_id": session_id,
        "thread_id": thread_id,
        "confirmation_id": confirmation_id,
    }
    if not all(isinstance(value, str) and value.strip() for value in context.values()):
        return {"ok": False, "error": "INVALID_ARGUMENTS"}
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须包含时区")
    now = now.astimezone(UTC)

    conn = get_connection()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            record = get_confirmation_for_context(conn, **context)

            if record is None:
                return {"ok": False, "error": "CONFIRMATION_NOT_FOUND"}
            if record["tool"] != "cancel_refund":
                return {"ok": False, "error": "CONFIRMATION_INVALID"}

            # 已撤回：重放保存结果，不再修改申请。
            if record["status"] == "consumed":
                try:
                    saved = json.loads(record["result_json"])
                except (TypeError, ValueError):
                    return {"ok": False, "error": "CONFIRMATION_INVALID"}
                if not isinstance(saved, dict) or saved.get("status") != "withdrawn":
                    return {"ok": False, "error": "CONFIRMATION_INVALID"}
                return saved

            if record["status"] == "expired":
                return {"ok": False, "error": "CONFIRMATION_EXPIRED"}
            if record["status"] != "pending":
                return {"ok": False, "error": "CONFIRMATION_NOT_PENDING"}

            params = _withdraw_params(record)
            if params is None:
                return {"ok": False, "error": "CONFIRMATION_INVALID"}

            expires_at = datetime.fromisoformat(record["expires_at"])
            if expires_at.tzinfo is None or expires_at.utcoffset() is None:
                return {"ok": False, "error": "CONFIRMATION_INVALID"}
            if now >= expires_at:
                close_pending_confirmation(conn, **context, status="expired")
                return {"ok": False, "error": "CONFIRMATION_EXPIRED"}

            # 撤回与内部结算可能同时到达，必须在写事务内复查申请状态。
            refund = get_after_sales_refund_for_withdraw(
                conn,
                user_id=user_id,
                order_id=params["order_id"],
                refund_id=params["refund_id"],
            )
            if refund is None:
                return {"ok": False, "error": "REFUND_NOT_FOUND"}
            if refund["status"] != "pending":
                return {
                    "ok": False,
                    "error": "REFUND_NOT_PENDING",
                    "status": refund["status"],
                }
            if refund["amount_cents"] != params["amount_cents"]:
                return {"ok": False, "error": "CONFIRMATION_STALE"}

            timestamp = now.isoformat()
            result = {
                "ok": True,
                "status": "withdrawn",
                "refund_id": refund["refund_id"],
                "order_id": refund["order_id"],
                "amount_cents": refund["amount_cents"],
            }
            withdraw_refund_in_transaction(
                conn,
                refund_id=refund["refund_id"],
                user_id=user_id,
                order_id=refund["order_id"],
                timestamp=timestamp,
            )
            consume_withdraw_confirmation(
                conn,
                context=context,
                result_json=json.dumps(result, ensure_ascii=False),
                timestamp=timestamp,
            )
        # 申请状态与确认结果在这里一起提交。
        return result
    finally:
        conn.close()


def settle_refund(*, refund_id: str, now: datetime) -> dict:
    """内部模拟结算：完成 pending 售后申请，并原子地把订单置为 refunded。

    仅由可信内部入口调用，不作为 Agent 工具暴露。重复调用返回已保存结果。
    """
    if not isinstance(refund_id, str) or not refund_id.strip():
        return {"ok": False, "error": "INVALID_ARGUMENTS"}
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须包含时区")
    now = now.astimezone(UTC)
    refund_id = refund_id.strip()

    conn = get_connection()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            refund = get_refund_for_settlement(conn, refund_id=refund_id)
            if refund is None:
                return {"ok": False, "error": "REFUND_NOT_FOUND"}

            # 取消退款在取消时已经完成，不能再次进入售后结算。
            if refund["kind"] != "after_sales":
                return {"ok": False, "error": "REFUND_KIND_NOT_SUPPORTED"}

            if refund["status"] == "completed":
                settlement = get_refund_settlement(conn, refund_id=refund_id)
                if (
                    settlement is None
                    or settlement["order_id"] != refund["order_id"]
                    or settlement["user_id"] != refund["user_id"]
                    or settlement["amount_cents"] != refund["amount_cents"]
                    or refund["order_status"] != "refunded"
                ):
                    return {"ok": False, "error": "STATE_INCONSISTENT"}
                return {
                    "ok": True,
                    "refund_id": refund["refund_id"],
                    "order_id": refund["order_id"],
                    "amount_cents": refund["amount_cents"],
                    "refund_status": "completed",
                    "order_status": "refunded",
                    "replayed": True,
                }

            if refund["status"] != "pending":
                return {
                    "ok": False,
                    "error": "REFUND_NOT_PENDING",
                    "status": refund["status"],
                }

            if refund["order_status"] not in {"shipped", "delivered"}:
                return {
                    "ok": False,
                    "error": "ORDER_STATUS_NOT_SUPPORTED",
                    "status": refund["order_status"],
                }

            # V1 只支持全额退款；金额在申请时已由服务端按实付金额确定。
            if refund["amount_cents"] != refund["paid_amount_cents"]:
                return {"ok": False, "error": "AMOUNT_MISMATCH"}

            timestamp = now.isoformat()
            result = {
                "ok": True,
                "refund_id": refund["refund_id"],
                "order_id": refund["order_id"],
                "amount_cents": refund["amount_cents"],
                "refund_status": "completed",
                "order_status": "refunded",
                "replayed": False,
            }

            complete_refund_in_transaction(
                conn,
                refund_id=refund_id,
                timestamp=timestamp,
            )
            refund_order_in_transaction(
                conn,
                user_id=refund["user_id"],
                order_id=refund["order_id"],
                expected_status=refund["order_status"],
            )
            insert_refund_settlement(
                conn,
                record={
                    "settlement_id": f"stl_{uuid4().hex}",
                    "refund_id": refund["refund_id"],
                    "order_id": refund["order_id"],
                    "user_id": refund["user_id"],
                    "amount_cents": refund["amount_cents"],
                    "previous_refund_status": "pending",
                    "new_refund_status": "completed",
                    "previous_order_status": refund["order_status"],
                    "new_order_status": "refunded",
                    "created_at": timestamp,
                },
            )

        # 退款、订单和结算记录在这里一起提交。
        return result
    finally:
        conn.close()


def get_refund_status(*, user_id: str, order_id: str) -> dict:
    """查询本人订单的最新售后申请；没有申请时返回 refund=None。"""
    order = get_order_for_user(user_id=user_id, order_id=order_id)
    if order is None:
        return {"ok": False, "error": "ORDER_NOT_FOUND"}

    refund = get_latest_after_sales_refund(
        user_id=user_id,
        order_id=order_id,
    )
    return {
        "ok": True,
        "order_id": order_id,
        "refund": refund,
    }
