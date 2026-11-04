"""真实 LLM 评测用例：只给用户消息、订单和期望数据库状态。"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_LLM_CASES_PATH = Path(__file__).with_name("llm_cases.jsonl")
CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
OUTCOMES = {"canceled", "rejected", "no_write", "refund_submitted"}


@dataclass(frozen=True)
class LLMEvalCase:
    case_id: str
    user_id: str
    order: dict[str, Any]
    user_messages: list[str]
    confirmation_message: str | None
    expected_tool_calls: list[dict[str, Any]]
    expected: dict[str, Any]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _parse_expected_tool_calls(case_id: str, raw: object) -> list[dict[str, Any]]:
    _require(
        isinstance(raw, list) and bool(raw),
        f"{case_id}: expected_tool_calls 必须是非空数组",
    )
    calls = []
    for index, item in enumerate(raw, start=1):
        _require(
            isinstance(item, dict),
            f"{case_id}: expected_tool_calls 第 {index} 项必须是对象",
        )
        tool = item.get("tool")
        _require(
            isinstance(tool, str) and bool(tool.strip()),
            f"{case_id}: expected_tool_calls 第 {index} 项缺少 tool",
        )
        if "ok" in item:
            _require(
                isinstance(item["ok"], bool),
                f"{case_id}: expected_tool_calls 第 {index} 项 ok 必须是布尔值",
            )
        if "error" in item:
            _require(
                isinstance(item["error"], str) and bool(item["error"]),
                f"{case_id}: expected_tool_calls 第 {index} 项 error 必须是非空字符串",
            )
        calls.append(dict(item))
    return calls


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
    if confirmation_message is not None:
        _require(
            isinstance(confirmation_message, str) and bool(confirmation_message.strip()),
            f"{case_id}: confirmation_message 必须是非空字符串或 null",
        )

    expected_tool_calls = _parse_expected_tool_calls(
        case_id,
        data.get("expected_tool_calls"),
    )

    expected = data.get("expected")
    _require(isinstance(expected, dict), f"{case_id}: expected 必须是对象")
    for key in ("outcome", "order_status", "refund_count", "confirmation_count", "operation_logs"):
        _require(key in expected, f"{case_id}: expected 缺少 {key}")
    outcome = expected["outcome"]
    _require(outcome in OUTCOMES, f"{case_id}: expected.outcome 必须是 {sorted(OUTCOMES)}")
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
        _require(expected["order_status"] == "canceled", f"{case_id}: 最终状态必须是 canceled")
        _require(expected["refund_count"] == 1, f"{case_id}: 必须恰好生成一条退款")
        _require(expected["confirmation_count"] == 1, f"{case_id}: 必须恰好有一条确认记录")
        _require(expected["operation_logs"] == 1, f"{case_id}: 必须恰好有一条操作日志")
        _require(confirmation_message is not None, f"{case_id}: 成功取消需要 confirmation_message")
        for key in ("refund_kind", "refund_amount_cents", "refund_status"):
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
        for key in ("confirmation_status", "confirmation_result_status"):
            _require(key in expected, f"{case_id}: expected 缺少 {key}")
            _require(
                isinstance(expected[key], str) and bool(expected[key]),
                f"{case_id}: expected.{key} 不能为空",
            )
    elif outcome == "refund_submitted":
        _require(order["status"] == "shipped", f"{case_id}: 退款提交用例必须是已发货订单")
        _require(
            expected["order_status"] == order["status"],
            f"{case_id}: 提交退款申请不应改变订单状态",
        )
        _require(expected["refund_count"] == 1, f"{case_id}: 必须恰好生成一条退款申请")
        _require(expected["confirmation_count"] == 1, f"{case_id}: 必须恰好有一条确认记录")
        _require(expected["operation_logs"] == 0, f"{case_id}: 提交退款申请不应写操作日志")
        _require(confirmation_message is not None, f"{case_id}: 退款提交需要 confirmation_message")
        _require(
            any(
                call["tool"] == "prepare_refund_confirmation"
                for call in expected_tool_calls
            ),
            f"{case_id}: 必须预期 prepare_refund_confirmation 调用",
        )
        for key in (
            "refund_kind",
            "refund_amount_cents",
            "refund_status",
            "confirmation_status",
            "confirmation_result_status",
        ):
            _require(key in expected, f"{case_id}: expected 缺少 {key}")
        _require(
            expected["refund_kind"] == "after_sales",
            f"{case_id}: refund_kind 必须是 after_sales",
        )
        _require(
            expected["refund_amount_cents"] == order["paid_amount_cents"],
            f"{case_id}: 退款金额必须等于订单实付金额",
        )
        _require(expected["refund_status"] == "pending", f"{case_id}: 退款申请必须是 pending")
        _require(
            expected["confirmation_status"] == "consumed",
            f"{case_id}: 退款确认状态必须是 consumed",
        )
        _require(
            expected["confirmation_result_status"] == "submitted",
            f"{case_id}: 退款确认结果必须是 submitted",
        )
    else:
        _require(
            expected["order_status"] == order["status"],
            f"{case_id}: 非取消用例的最终状态应与初始订单状态一致",
        )
        _require(expected["refund_count"] == 0, f"{case_id}: 非取消用例不能生成退款")
        _require(expected["operation_logs"] == 0, f"{case_id}: 非取消用例不能写操作日志")
        if outcome == "no_write":
            _require(expected["confirmation_count"] == 0, f"{case_id}: 只读用例不能生成确认记录")
            _require(confirmation_message is None, f"{case_id}: 只读用例不应有 confirmation_message")
        elif confirmation_message is None:
            _require(expected["confirmation_count"] == 0, f"{case_id}: 没有确认消息时不能有确认记录")
        else:
            _require(expected["confirmation_count"] == 1, f"{case_id}: 拒绝确认必须恰好有一条确认记录")
            _require(
                expected.get("confirmation_status") == "rejected",
                f"{case_id}: 拒绝确认的 confirmation_status 必须是 rejected",
            )

    normalized_expected = dict(expected)
    normalized_expected["other_orders_canceled"] = other_orders

    return LLMEvalCase(
        case_id=case_id,
        user_id=user_id,
        order=order,
        user_messages=list(user_messages),
        confirmation_message=confirmation_message,
        expected_tool_calls=expected_tool_calls,
        expected=normalized_expected,
    )


def _read_case_data(path: Path) -> Iterator[tuple[int, dict]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"无法读取用例文件: {path}") from exc

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"用例文件第 {line_number} 行不是合法 JSON: {path}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"用例文件第 {line_number} 行必须是 JSON 对象: {path}")
        yield line_number, data


def load_llm_case(case_id: str, path: Path | None = None) -> LLMEvalCase:
    """按 case_id 读取一条真实 LLM 用例。"""
    cases_path = path if path is not None else DEFAULT_LLM_CASES_PATH
    for _, data in _read_case_data(cases_path):
        if data.get("case_id") == case_id:
            return _parse_case(data)
    raise ValueError(f"未找到真实 LLM 用例 {case_id}（文件: {cases_path}）")


def load_llm_cases(path: Path | None = None) -> list[LLMEvalCase]:
    """读取并校验全部真实 LLM 用例。"""
    cases_path = path if path is not None else DEFAULT_LLM_CASES_PATH
    cases: list[LLMEvalCase] = []
    seen: set[str] = set()
    for _, data in _read_case_data(cases_path):
        case = _parse_case(data)
        if case.case_id in seen:
            raise ValueError(f"用例编号重复: {case.case_id}（文件: {cases_path}）")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError(f"用例文件没有有效用例: {cases_path}")
    return cases