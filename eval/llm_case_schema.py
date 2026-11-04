"""真实 LLM 冒烟用例：只提供用户消息和期望数据库状态。"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_LLM_CASES_PATH = Path(__file__).with_name("llm_cases.jsonl")
CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class LLMEvalCase:
    case_id: str
    user_id: str
    order: dict[str, Any]
    user_messages: list[str]
    confirmation_message: str
    expected: dict[str, Any]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _parse_case(data: dict) -> LLMEvalCase:
    case_id = data.get("case_id")
    user_id = data.get("user_id")
    _require(isinstance(case_id, str) and bool(case_id.strip()), "case_id 不能为空")
    _require(
        CASE_ID_PATTERN.fullmatch(case_id) is not None,
        f"case_id 只能包含字母、数字、下划线和连字符: {case_id}",
    )
    _require(
        isinstance(user_id, str) and bool(user_id.strip()),
        f"{case_id}: user_id 不能为空",
    )

    order = data.get("order")
    _require(isinstance(order, dict), f"{case_id}: order 必须是对象")
    for key in ("order_id", "status", "paid_amount_cents", "paid_minutes_ago"):
        _require(key in order, f"{case_id}: order 缺少 {key}")
    _require(
        isinstance(order["order_id"], str) and bool(order["order_id"].strip()),
        f"{case_id}: order.order_id 不能为空",
    )
    _require(order["status"] == "paid", f"{case_id}: 当前冒烟用例只支持已付款订单")
    _require(
        type(order["paid_amount_cents"]) is int and order["paid_amount_cents"] > 0,
        f"{case_id}: order.paid_amount_cents 必须是正整数",
    )
    _require(
        type(order["paid_minutes_ago"]) is int and order["paid_minutes_ago"] >= 0,
        f"{case_id}: order.paid_minutes_ago 必须是非负整数",
    )

    user_messages = data.get("user_messages")
    _require(
        isinstance(user_messages, list) and bool(user_messages),
        f"{case_id}: user_messages 必须是非空数组",
    )
    _require(
        all(isinstance(message, str) and bool(message.strip()) for message in user_messages),
        f"{case_id}: user_messages 每项都必须是非空字符串",
    )

    confirmation_message = data.get("confirmation_message")
    _require(
        isinstance(confirmation_message, str) and bool(confirmation_message.strip()),
        f"{case_id}: confirmation_message 不能为空",
    )

    expected = data.get("expected")
    _require(isinstance(expected, dict), f"{case_id}: expected 必须是对象")
    for key in (
        "outcome",
        "order_status",
        "refund_count",
        "refund_kind",
        "refund_amount_cents",
        "refund_status",
        "confirmation_count",
        "confirmation_status",
        "confirmation_result_status",
        "operation_logs",
    ):
        _require(key in expected, f"{case_id}: expected 缺少 {key}")
    _require(expected["outcome"] == "canceled", f"{case_id}: 当前冒烟用例只支持取消成功")
    _require(expected["order_status"] == "canceled", f"{case_id}: 最终状态必须是 canceled")
    _require(expected["refund_count"] == 1, f"{case_id}: 必须恰好生成一条退款")
    _require(expected["refund_kind"] == "cancellation", f"{case_id}: refund_kind 必须是 cancellation")
    _require(
        type(expected["refund_amount_cents"]) is int and expected["refund_amount_cents"] >= 0,
        f"{case_id}: expected.refund_amount_cents 必须是非负整数",
    )
    _require(
        isinstance(expected["refund_status"], str) and bool(expected["refund_status"]),
        f"{case_id}: expected.refund_status 不能为空",
    )
    _require(expected["confirmation_count"] == 1, f"{case_id}: 必须恰好有一条确认记录")
    _require(
        isinstance(expected["confirmation_status"], str) and bool(expected["confirmation_status"]),
        f"{case_id}: expected.confirmation_status 不能为空",
    )
    _require(
        isinstance(expected["confirmation_result_status"], str)
        and bool(expected["confirmation_result_status"]),
        f"{case_id}: expected.confirmation_result_status 不能为空",
    )
    _require(expected["operation_logs"] == 1, f"{case_id}: 必须恰好有一条操作日志")

    other_orders = expected.get("other_orders_canceled", 0)
    _require(
        type(other_orders) is int and other_orders >= 0,
        f"{case_id}: expected.other_orders_canceled 必须是非负整数",
    )
    normalized_expected = dict(expected)
    normalized_expected["other_orders_canceled"] = other_orders

    return LLMEvalCase(
        case_id=case_id,
        user_id=user_id,
        order=order,
        user_messages=list(user_messages),
        confirmation_message=confirmation_message,
        expected=normalized_expected,
    )


def load_llm_case(case_id: str, path: Path | None = None) -> LLMEvalCase:
    """按 case_id 读取一条真实 LLM 用例。"""
    cases_path = path if path is not None else DEFAULT_LLM_CASES_PATH
    try:
        lines = cases_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"无法读取用例文件: {cases_path}") from exc

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"用例文件第 {line_number} 行不是合法 JSON: {cases_path}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"用例文件第 {line_number} 行必须是 JSON 对象: {cases_path}")
        if data.get("case_id") == case_id:
            return _parse_case(data)

    raise ValueError(f"未找到真实 LLM 用例 {case_id}（文件: {cases_path}）")