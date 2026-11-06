"""业务状态枚举到中文表述的唯一映射。

英文枚举仍是数据层契约，供程序判断与前端筛选使用；
但模型回复、确认摘要和确定性回复都必须使用这里的中文表述。
"""

ORDER_STATUS_TEXT = {
    "unpaid": "未付款",
    "paid": "已付款",
    "shipped": "已发货",
    "delivered": "已送达",
    "canceled": "已取消",
    "refunded": "已退款",
}

REFUND_STATUS_TEXT = {
    "pending": "待处理",
    "approved": "审核通过",
    "rejected": "审核拒绝",
    "completed": "已完成",
    "failed": "结算失败",
    "withdrawn": "已撤回",
}


def order_status_text(status) -> str:
    """未知状态保留原值，避免把缺失数据说成某个确定状态。"""
    return ORDER_STATUS_TEXT.get(status, status or "未记录")


def refund_status_text(status) -> str:
    return REFUND_STATUS_TEXT.get(status, status or "未记录")


def with_order_status_text(order: dict) -> dict:
    """为工具结果补充中文状态；不改写 status 本身。"""
    return {**order, "status_text": order_status_text(order.get("status"))}


def with_refund_status_text(refund: dict) -> dict:
    """退款申请同时给出申请状态与所属订单状态的中文表述。"""
    result = {**refund, "status_text": refund_status_text(refund.get("status"))}
    if "order_status" in refund:
        result["order_status_text"] = order_status_text(refund["order_status"])
    return result
