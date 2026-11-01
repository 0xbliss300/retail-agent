"""取消订单的业务服务：资格复查、确认绑定、事务、审计与结果重放。"""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from app.business.policies import check_cancel_window
from app.business.repository import (
    cancel_order_in_transaction,
    close_pending_confirmation,
    consume_cancel_confirmation,
    get_blocking_refund_for_cancel,
    get_cancel_log_for_order,
    get_confirmation_for_context,
    get_order_for_user,
    insert_cancel_operation_log,
    insert_cancellation_refund,
)
from app.storage.database import get_connection


def _denied(code: str, reason: str) -> dict:
    return {"ok": True, "eligible": False, "policy_code": code, "reason": reason}


def check_cancel_eligibility(
    *,
    user_id: str,
    order_id: str,
    now: datetime,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """准备时只读检查；执行时传入写事务连接重新检查。"""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须包含时区")
    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        if owns_connection:
            conn.execute("BEGIN")
        order = get_order_for_user(user_id=user_id, order_id=order_id, conn=conn)
        if order is None:
            return {"ok": False, "error": "ORDER_NOT_FOUND"}
        window = check_cancel_window(
            status=order["status"],
            paid_at=order["paid_at"],
            now=now,
            shipped_at=order["shipped_at"],
            delivered_at=order["delivered_at"],
        )
        if not window["window_open"]:
            return _denied(window["code"], window["reason"])
        amount = order["paid_amount_cents"]
        if type(amount) is not int or amount < 0 or (order["status"] == "unpaid" and amount != 0):
            return _denied("PAYMENT_INVALID", "订单状态与实付金额不一致，不能执行取消。")
        if get_cancel_log_for_order(conn, user_id=user_id, order_id=order_id) is not None:
            return _denied("ALREADY_CANCELED", "该订单已有取消执行记录，不可重复取消。")
        if get_blocking_refund_for_cancel(conn, user_id=user_id, order_id=order_id) is not None:
            return _denied("REFUND_EXISTS", "订单已有退款记录或处理中申请，不能重复进行取消退款。")
        return {
            "ok": True,
            "eligible": True,
            "policy_code": "ELIGIBLE",
            "reason": window["reason"],
            "order_status": order["status"],
            "amount_cents": amount,
            "paid_at": order["paid_at"],
        }
    finally:
        if owns_connection:
            conn.close()


def _valid_params(record: dict) -> dict | None:
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
        or set(params) != {"order_id", "reason", "order_status", "amount_cents", "paid_at"}
        or params["order_id"] != record["order_id"]
        or not isinstance(params["reason"], str)
        or not params["reason"].strip()
        or params["order_status"] not in ("unpaid", "paid")
        or type(params["amount_cents"]) is not int
        or params["amount_cents"] < 0
        or (params["paid_at"] is not None and not isinstance(params["paid_at"], str))
        or record["idempotency_key"] != f"confirm:{record['confirmation_id']}"
    ):
        return None
    return params


def approve_cancel_confirmation(
    *,
    user_id: str,
    session_id: str,
    thread_id: str,
    confirmation_id: str,
    now: datetime,
) -> dict:
    """仅供可信入口在用户明确确认后调用，不向模型开放批准凭证。"""
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
            if record["tool"] != "cancel_order":
                return {"ok": False, "error": "CONFIRMATION_INVALID"}
            params = _valid_params(record)
            if params is None:
                return {"ok": False, "error": "CONFIRMATION_INVALID"}

            # 已完成时按持久化结果重放，不再检查当前取消窗口或再次写订单。
            if record["status"] == "consumed":
                log = get_cancel_log_for_order(conn, user_id=user_id, order_id=record["order_id"])
                if log is None or log["confirmation_id"] != confirmation_id:
                    return {"ok": False, "error": "CONFIRMATION_INVALID"}
                if log["params_fingerprint"] != record["params_fingerprint"]:
                    return {"ok": False, "error": "IDEMPOTENCY_CONFLICT"}
                try:
                    saved = json.loads(record["result_json"])
                except (TypeError, ValueError):
                    return {"ok": False, "error": "CONFIRMATION_INVALID"}
                if not isinstance(saved, dict) or saved.get("status") != "canceled":
                    return {"ok": False, "error": "CONFIRMATION_INVALID"}
                return saved
            if record["status"] == "expired":
                return {"ok": False, "error": "CONFIRMATION_EXPIRED"}
            if record["status"] != "pending":
                return {"ok": False, "error": "CONFIRMATION_NOT_PENDING"}
            try:
                expires = datetime.fromisoformat(record["expires_at"])
                created = datetime.fromisoformat(record["created_at"])
            except (TypeError, ValueError):
                return {"ok": False, "error": "CONFIRMATION_INVALID"}
            if expires.utcoffset() is None or created.utcoffset() is None or now < created:
                return {"ok": False, "error": "CONFIRMATION_INVALID"}
            if now >= expires:
                close_pending_confirmation(conn, **context, status="expired")
                return {"ok": False, "error": "CONFIRMATION_EXPIRED"}

            eligibility = check_cancel_eligibility(
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
            if any(eligibility[key] != params[key] for key in ("order_status", "amount_cents", "paid_at")):
                return {"ok": False, "error": "CONFIRMATION_STALE"}

            timestamp = now.isoformat()
            refund = None
            cancel_order_in_transaction(
                conn,
                user_id=user_id,
                order_id=record["order_id"],
                expected_status=params["order_status"],
            )
            if params["order_status"] == "paid":
                refund = {
                    "refund_id": f"rf_{uuid4().hex}",
                    "kind": "cancellation",
                    "amount_cents": params["amount_cents"],
                    "status": "completed",
                }
                insert_cancellation_refund(
                    conn,
                    record={
                        **refund,
                        **context,
                        "order_id": record["order_id"],
                        "reason": params["reason"],
                        "idempotency_key": record["idempotency_key"],
                        "timestamp": timestamp,
                    },
                )
            insert_cancel_operation_log(
                conn,
                record={
                    **context,
                    "log_id": f"op_{uuid4().hex}",
                    "order_id": record["order_id"],
                    "previous_status": params["order_status"],
                    "amount_cents": params["amount_cents"],
                    "reason": params["reason"],
                    "params_fingerprint": record["params_fingerprint"],
                    "idempotency_key": record["idempotency_key"],
                    "timestamp": timestamp,
                },
            )
            result = {"ok": True, "status": "canceled", "order_id": record["order_id"], "refund": refund}
            consume_cancel_confirmation(
                conn,
                context=context,
                timestamp=timestamp,
                result_json=json.dumps(result, ensure_ascii=False),
            )
        return result
    finally:
        conn.close()
