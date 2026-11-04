"""工具执行的重试决策：只读工具可安全重试，写工具不自动重试。"""

READ_ONLY_TOOLS = frozenset({
    "list_orders",
    "get_logistics",
    "get_order",
    "get_policy",
    "check_refund_eligibility",
    "get_refund_status",
})

WRITE_TOOLS = frozenset({
    "cancel_order",
    "prepare_refund_confirmation",
    "withdraw_refund",
})

# 只读工具遇到超时或数据库忙时可以安全重试；业务拒绝和普通异常不重试。
RETRYABLE_ERRORS = frozenset({"TOOL_TIMEOUT", "TOOL_BUSY"})
READ_TOOL_MAX_RETRIES = 1


def attempts_for(tool_name: str) -> int:
    """只读工具最多执行一次加一次重试；写工具只执行一次。"""
    return 1 + READ_TOOL_MAX_RETRIES if tool_name in READ_ONLY_TOOLS else 1


def should_retry(tool_name: str, result: dict) -> bool:
    """仅对只读工具的超时或数据库忙结果重试。"""
    return (
        tool_name in READ_ONLY_TOOLS
        and isinstance(result, dict)
        and result.get("error") in RETRYABLE_ERRORS
    )