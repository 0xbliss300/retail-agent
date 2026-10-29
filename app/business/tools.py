"""模型可见的工具描述，以及到业务 Service 的薄适配。"""

from app.business import service
from datetime import datetime

# 给模型看的工具说明；真正的数据库查询由下面的函数执行。
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "查询当前用户的订单信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "订单号，例如 ORD-1001。",
                    }
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_policy",
            "description": (
                "查询通用的取消或退款政策。"
                "不能用于判断某笔具体订单是否符合条件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "enum": ["cancellation", "refund"],
                        "description": (
                            "cancellation 表示取消政策，"
                            "refund 表示退款政策。"
                        ),
                    }
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_refund_eligibility",
            "description": (
                "判断当前用户的具体订单是否符合退款申请条件。"
                "仅查询资格，不提交申请、不执行退款。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "订单号，例如 ORD-2002。",
                    }
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
]

def get_policy(topic: str) -> dict:
    return service.get_policy(topic=topic)

# 查询订单 返回字典
def get_order(user_id: str, order_id: str) -> dict:
    return service.get_order(user_id=user_id, order_id=order_id)

# 查询退款是否符合规则 返回字典
def check_refund_eligibility(
        user_id: str,
        order_id: str,
        *,
        now: datetime,
) -> dict:
    return service.check_refund_eligibility(
        user_id=user_id,
        order_id=order_id,
        now=now
    )

