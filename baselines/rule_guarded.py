"""B 档装配：保留授权与业务规则，跳过用户确认与审计。

仅供评测使用。生产入口固定使用 C 档（确认 + 幂等 + 恢复）。
本模块不导出通用函数，也不被 app 包引用。
"""

from datetime import UTC, datetime
from uuid import uuid4

from app.business.cancellation import check_cancel_eligibility
from app.business.repository import (
    cancel_order_in_transaction,
    insert_cancellation_refund,
)
from app.storage.database import get_connection


def cancel_without_confirmation(
    *,
    user_id: str,
    order_id: str,
    reason: str,
    now: datetime,
) -> dict:
    """直接执行取消：仍然校验归属与规则，但不生成待确认记录、不写审计日志。

    缺少审计日志不是遗漏：operation_logs 对 confirmations 有复合外键，
    没有确认记录就无法写入。这一点会在对比报告中如实说明。
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须包含时区")
    now = now.astimezone(UTC)

    eligibility = check_cancel_eligibility(user_id=user_id, order_id=order_id, now=now)
    if not eligibility["ok"]:
        return eligibility
    if not eligibility["eligible"]:
        return {
            "ok": False,
            "error": "POLICY_DENIED",
            "policy_code": eligibility["policy_code"],
            "reason": eligibility["reason"],
        }

    timestamp = now.isoformat()
    conn = get_connection()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            cancel_order_in_transaction(
                conn,
                user_id=user_id,
                order_id=order_id,
                expected_status=eligibility["order_status"],
            )
            if eligibility["order_status"] == "paid":
                insert_cancellation_refund(
                    conn,
                    record={
                        "refund_id": f"rf_{uuid4().hex}",
                        "order_id": order_id,
                        "user_id": user_id,
                        "amount_cents": eligibility["amount_cents"],
                        "reason": reason,
                        "idempotency_key": None,
                        "confirmation_id": None,
                        "timestamp": timestamp,
                    },
                )
    finally:
        conn.close()

    return {
        "ok": True,
        "status": "canceled",
        "order_id": order_id,
        "refund_amount_cents": (
            eligibility["amount_cents"]
            if eligibility["order_status"] == "paid"
            else 0
        ),
    }
