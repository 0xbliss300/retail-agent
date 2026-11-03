"""最小评测用例：从 JSONL 加载并校验取消成功和拒绝用例。"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CASES_PATH = Path(__file__).with_name("dev_cases.jsonl")
OUTCOMES = {"canceled", "rejected"}


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
    _require(
        order["status"] in {"paid", "shipped"},
        f"{case_id}: order.status 只支持 paid 或 shipped",
    )
    _require(
        type(order["paid_amount_cents"]) is int and order["paid_amount_cents"] > 0,
        f"{case_id}: order.paid_amount_cents 必须是正整数",
    )
    _require(
        type(order["paid_minutes_ago"]) is int and order["paid_minutes_ago"] >= 0,
        f"{case_id}: order.paid_minutes_ago 必须是非负整数",
    )
    if order["status"] == "shipped":
        _require("shipped_minutes_ago" in order, f"{case_id}: 已发货订单缺少 shipped_minutes_ago")
        _require(
            type(order["shipped_minutes_ago"]) is int and order["shipped_minutes_ago"] >= 0,
            f"{case_id}: order.shipped_minutes_ago 必须是非负整数",
        )
        _require(
            order["shipped_minutes_ago"] <= order["paid_minutes_ago"],
            f"{case_id}: 发货时间不能早于付款时间",
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
    outcome = expected.get("outcome")
    _require(outcome in OUTCOMES, f"{case_id}: expected.outcome 必须是 canceled 或 rejected")
    for key in ("order_status", "refund_count", "confirmation_count", "operation_logs"):
        _require(key in expected, f"{case_id}: expected 缺少 {key}")
    _require(
        isinstance(expected["order_status"], str) and bool(expected["order_status"]),
        f"{case_id}: expected.order_status 不能为空",
    )
    _require(
        type(expected["refund_count"]) is int and expected["refund_count"] >= 0,
        f"{case_id}: expected.refund_count 必须是非负整数",
    )
    _require(
        type(expected["confirmation_count"]) is int and expected["confirmation_count"] >= 0,
        f"{case_id}: expected.confirmation_count 必须是非负整数",
    )
    _require(
        type(expected["operation_logs"]) is int and expected["operation_logs"] >= 0,
        f"{case_id}: expected.operation_logs 必须是非负整数",
    )
    other_orders = expected.get("other_orders_canceled", 0)
    _require(
        type(other_orders) is int and other_orders >= 0,
        f"{case_id}: expected.other_orders_canceled 必须是非负整数",
    )

    if outcome == "canceled":
        _require(order["status"] == "paid", f"{case_id}: 成功取消用例必须是已付款订单")
        _require(expected["order_status"] == "canceled", f"{case_id}: 成功取消的最终状态必须是 canceled")
        _require(expected["refund_count"] == 1, f"{case_id}: 成功取消必须恰好生成一条退款")
        _require(expected["confirmation_count"] == 1, f"{case_id}: 成功取消必须恰好有一条确认记录")
        _require(expected["operation_logs"] == 1, f"{case_id}: 成功取消必须恰好有一条操作日志")
        for key in (
            "refund_kind",
            "refund_amount_cents",
            "refund_status",
            "confirmation_status",
            "confirmation_result_status",
        ):
            _require(key in expected, f"{case_id}: expected 缺少 {key}")
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
            isinstance(expected["confirmation_result_status"], str)
            and bool(expected["confirmation_result_status"]),
            f"{case_id}: expected.confirmation_result_status 不能为空",
        )
    else:
        _require(
            expected["order_status"] == order["status"],
            f"{case_id}: 拒绝用例的最终状态应与初始订单状态一致",
        )
        _require(expected["refund_count"] == 0, f"{case_id}: 拒绝用例不能生成退款")
        _require(expected["confirmation_count"] == 0, f"{case_id}: 拒绝用例不能生成确认记录")
        _require(expected["operation_logs"] == 0, f"{case_id}: 拒绝用例不能写操作日志")

    normalized_expected = dict(expected)
    normalized_expected["other_orders_canceled"] = other_orders

    return EvalCase(
        case_id=case_id,
        user_id=user_id,
        user_message=user_message,
        order=order,
        tool_call=tool_call,
        expected=normalized_expected,
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