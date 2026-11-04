"""模型可见的工具描述，以及到业务 Service 的薄适配。"""

from datetime import datetime

from app.business import service

# 给模型看的工具说明；真正的数据库查询由下面的函数执行。
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "cancel_order",
            "description": (
                "用户明确要求取消本人订单时，提供订单号和用户说明的取消原因，准备待确认取消操作。"
                "未付款可取消；已付款须付款不超过24小时且未发货。"
                "本次调用仅保存摘要，用户另行明确确认后由运行时执行；不得声称订单已取消。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "用户要取消的订单号。"},
                    "reason": {"type": "string", "description": "用户提供的取消原因，不可编造。"},
                },
                "required": ["order_id", "reason"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_orders",
            "description": (
                "分页查询当前用户本人的订单列表，不需要用户提供订单号。"
                "返回订单号、状态、实付金额及时间；has_more和next_offset指示下一页。"
                "按订单号排序，仅查询，不修改订单。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer", "minimum": 1, "maximum": 50,
                        "description": "每页条数，默认10，最多50。",
                    },
                    "offset": {
                        "type": "integer", "minimum": 0, "maximum": 9223372036854775807,
                        "description": "跳过的本人订单数，默认0；下一页使用next_offset。",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_logistics",
            "description": (
                "查询当前用户指定订单的本地模拟物流，包括承运商、运单号和按时间排序的轨迹。"
                "仅查询，不修改订单、不发起退款；没有物流记录时返回logistics=null。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "订单号，例如ORD-2002。"},
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
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
    {
        "type": "function",
        "function": {
            "name": "prepare_refund_confirmation",
            "description": (
                "用户明确要求申请退款时，准备待确认操作。"
                "必须提供订单号及用户说明的原因；不提交退款申请、不执行退款。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "订单号，例如 ORD-2002。"},
                    "reason": {"type": "string", "description": "用户提供的退款原因，不可编造。"},
                },
                "required": ["order_id", "reason"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_refund_status",
            "description": (
                "查询当前用户指定订单的最新售后退款申请及状态。"
                "仅查询，不创建申请、不执行退款。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "订单号，例如 ORD-2002。",
                    },
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "withdraw_refund",
            "description": (
                "用户明确要求撤回尚未处理的退款申请时，准备待确认撤回操作。"
                "只撤回状态为pending的申请，不改变订单状态，不需要退款原因。"
                "本次调用仅保存摘要，用户另行明确确认后由运行时执行；"
                "不得声称申请已撤回。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "已有待处理退款申请的订单号，例如 ORD-2002。",
                    },
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
]

def list_orders(user_id: str, *, limit: int = 10, offset: int = 0) -> dict:
    return service.list_orders(user_id=user_id, limit=limit, offset=offset)


def get_logistics(user_id: str, order_id: str) -> dict:
    return service.get_logistics(user_id=user_id, order_id=order_id)


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

# 用于agent调用获取状态
def get_refund_status(user_id: str, order_id: str) -> dict:
    return service.get_refund_status(
        user_id=user_id,
        order_id=order_id,
    )
