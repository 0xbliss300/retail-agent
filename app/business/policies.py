"""售后业务规则"""

from datetime import UTC, datetime, timedelta

REFUND_WINDOW = timedelta(days=7)
CANCELLATION_WINDOW = timedelta(hours=24)

# 本项目的模拟业务政策，与纯函数规则一同维护。
POLICIES = {
    "cancellation": (
        "未付款订单可以取消。"
        "已付款且未发货的订单，在付款后24小时内可以取消。"
        "已发货或已送达的订单不能取消。"
        "取消订单需要用户确认。"
    ),
    "refund": (
        "已发货订单，在发货后7天内可以申请退款。"
        "已送达订单，在签收后7天内可以申请退款。"
        "同一订单已有处理中退款申请时，不可重复申请。"
        "当前只支持全额退款，提交申请需要用户确认。"
        "申请提交成功不代表退款已经到账。"
    ),
}

def check_cancel_window(
    *, status: str, paid_at: str | None, now: datetime,
    shipped_at: str | None = None, delivered_at: str | None = None,
) -> dict:
    """取消仅允许未付款或24小时内已付款未发货；恰好24小时仍允许。"""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须包含时区")
    if status not in {"unpaid", "paid"}:
        return {"window_open": False, "code": "STATUS_NOT_SUPPORTED", "reason": "当前订单状态不允许取消。"}
    if shipped_at is not None or delivered_at is not None:
        return {
            "window_open": False, "code": "ORDER_ALREADY_FULFILLED",
            "reason": "订单已有发货或签收记录，不允许取消。",
        }
    if status == "unpaid":
        if paid_at is not None:
            return {
                "window_open": False, "code": "PAYMENT_STATE_MISMATCH",
                "reason": "未付款状态与付款记录不一致。",
            }
    else:
        if paid_at is None:
            return {
                "window_open": False, "code": "TIME_MISSING", "reason": "缺少付款时间，无法确认取消期限。",
            }
        try:
            paid = datetime.fromisoformat(paid_at)
        except (TypeError, ValueError):
            return {"window_open": False, "code": "TIME_INVALID", "reason": "付款时间格式不正确。"}
        if paid.tzinfo is None or paid.utcoffset() is None:
            return {"window_open": False, "code": "TIME_INVALID", "reason": "付款时间缺少时区。"}
        elapsed = now - paid
        if elapsed < timedelta(0):
            return {"window_open": False, "code": "TIME_IN_FUTURE", "reason": "付款时间晚于当前时间。"}
        if elapsed > CANCELLATION_WINDOW:
            return {"window_open": False, "code": "WINDOW_EXPIRED", "reason": "付款已超过24小时，不能取消。"}
    return {"window_open": True, "code": "ELIGIBLE", "reason": "当前订单符合取消条件，执行仍需用户确认。"}


def check_refund_window(
        *,
        status: str,
        shipped_at: str | None,
        delivered_at: str | None,
        now: datetime,
) -> dict:
    """判断退款时间窗口；恰好满 7 天仍允许，超过则拒绝。"""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须包含时区")

    if status == "shipped":
        started_at = shipped_at
    elif status == "delivered":
        started_at = delivered_at
    else:
        return {
            "window_open": False,
            "code": "STATUS_NOT_SUPPORTED",
            "reason": "当前订单状态不支持售后退款申请。",
        }

    if started_at is None:
        return {
            "window_open": False,
            "code": "TIME_MISSING",
            "reason": "缺少业务时间，无法判断退款期限。",
        }

    try:
        start = datetime.fromisoformat(started_at)
    except (TypeError, ValueError):
        return {
            "window_open": False,
            "code": "TIME_INVALID",
            "reason": "业务时间格式不正确。",
        }

    if start.tzinfo is None or start.utcoffset() is None:
        return {
            "window_open": False,
            "code": "TIME_INVALID",
            "reason": "业务时间缺少时区。",
        }
    
    # 计算时间
    elapsed = (
        now.astimezone(UTC)
        - start.astimezone(UTC)
    )
    
    if elapsed < timedelta(0):
        return {
            "window_open": False,
            "code": "TIME_IN_FUTURE",
            "reason": "业务时间晚于当前时间，请核查订单数据。",
        }

    if elapsed > REFUND_WINDOW:
        return {
            "window_open": False,
            "code": "WINDOW_EXPIRED",
            "reason": "已超过 7 天退款申请期限。",
        }

    return {
        "window_open": True,
        "code": "WITHIN_WINDOW",
        "reason": "处于 7 天退款申请期限内。",
    }
