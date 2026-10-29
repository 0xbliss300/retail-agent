"""组合数据访问与业务规则，供工具或服务端入口调用。"""

from datetime import datetime

from app.business.policies import POLICIES, check_refund_window
from app.business.repository import get_active_refund, get_order_for_user


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
) -> dict:
    """只读资格预检；实际创建申请时仍须在写事务中重新校验。"""
    order = get_order_for_user(user_id=user_id, order_id=order_id)
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

    active_refund = get_active_refund(user_id=user_id, order_id=order_id)
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
