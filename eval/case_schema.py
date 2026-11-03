"""最小评测用例：从 JSONL 加载并校验一条取消用例。"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CASES_PATH = Path(__file__).with_name("dev_cases.jsonl")


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    user_id: str
    user_message: str
    order: dict[str, Any]
    tool_call: dict[str, Any]
    expected: dict[str, Any]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _parse_case(data: dict) -> EvalCase:
    case_id = data.get("case_id")
    user_id = data.get("user_id")
    user_message = data.get("user_message")
    _require(isinstance(case_id, str) and bool(case_id.strip()), "case_id 不能为空")
    _require(
        isinstance(user_id, str) and bool(user_id.strip()),
        f"{case_id}: user_id 不能为空",
    )
    _require(
        isinstance(user_message, str) and bool(user_message.strip()),
        f"{case_id}: user_message 不能为空",
    )

    order = data.get("order")
    _require(isinstance(order, dict), f"{case_id}: order 必须是对象")
    for key in ("order_id", "status", "paid_amount_cents", "paid_minutes_ago"):
        _require(key in order, f"{case_id}: order 缺少 {key}")
    _require(
        isinstance(order["order_id"], str) and bool(order["order_id"].strip()),
        f"{case_id}: order.order_id 不能为空",
    )
    _require(order["status"] == "paid", f"{case_id}: 当前最小评测只支持已付款订单")
    _require(
        type(order["paid_amount_cents"]) is int and order["paid_amount_cents"] > 0,
        f"{case_id}: order.paid_amount_cents 必须是正整数",
    )
    _require(
        type(order["paid_minutes_ago"]) is int and order["paid_minutes_ago"] >= 0,
        f"{case_id}: order.paid_minutes_ago 必须是非负整数",
    )

    tool_call = data.get("tool_call")
    _require(isinstance(tool_call, dict), f"{case_id}: tool_call 必须是对象")
    _require(tool_call.get("name") == "cancel_order", f"{case_id}: 当前最小评测只支持 cancel_order")
    arguments = tool_call.get("arguments")
    _require(isinstance(arguments, dict), f"{case_id}: tool_call.arguments 必须是对象")
    _require(
        arguments.get("order_id") == order["order_id"],
        f"{case_id}: 工具参数 order_id 与用例订单不一致",
    )
    _require(
        isinstance(arguments.get("reason"), str) and bool(arguments["reason"].strip()),
        f"{case_id}: 工具参数 reason 不能为空",
    )

    expected = data.get("expected")
    _require(isinstance(expected, dict), f"{case_id}: expected 必须是对象")
    for key in (
        "order_status",
        "refund_kind",
        "refund_amount_cents",
        "refund_status",
        "confirmation_status",
        "operation_logs",
    ):
        _require(key in expected, f"{case_id}: expected 缺少 {key}")
    _require(expected["order_status"] == "canceled", f"{case_id}: 当前最小评测只支持取消结果")
    _require(expected["refund_kind"] == "cancellation", f"{case_id}: refund_kind 必须是 cancellation")
    _require(
        type(expected["refund_amount_cents"]) is int and expected["refund_amount_cents"] >= 0,
        f"{case_id}: expected.refund_amount_cents 必须是非负整数",
    )
    _require(
        isinstance(expected["refund_status"], str) and bool(expected["refund_status"]),
        f"{case_id}: expected.refund_status 不能为空",
    )
    _require(
        isinstance(expected["confirmation_status"], str) and bool(expected["confirmation_status"]),
        f"{case_id}: expected.confirmation_status 不能为空",
    )
    _require(
        type(expected["operation_logs"]) is int and expected["operation_logs"] >= 1,
        f"{case_id}: expected.operation_logs 必须是正整数",
    )

    return EvalCase(
        case_id=case_id,
        user_id=user_id,
        user_message=user_message,
        order=order,
        tool_call=tool_call,
        expected=expected,
    )


def load_case(case_id: str, path: Path | None = None) -> EvalCase:
    """按 case_id 读取一条用例；文件缺失或内容不合法时抛出 ValueError。"""
    cases_path = path if path is not None else DEFAULT_CASES_PATH
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

    raise ValueError(f"未找到用例 {case_id}（文件: {cases_path}）")