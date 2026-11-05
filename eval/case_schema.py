"""最小评测用例：从 JSONL 加载并校验取消、拒绝、退款申请和撤回用例。"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval.fault_injection import FaultConfig, parse_fault

DEFAULT_CASES_PATH = Path(__file__).with_name("dev_cases.jsonl")
OUTCOMES = {"canceled", "rejected", "refund_submitted", "refund_withdrawn"}
CONFIRMATION_MODES = {"explicit", "chat", "none"}
WRITE_TOOLS = {"cancel_order", "prepare_refund_confirmation", "withdraw_refund"}
REASON_TOOLS = {"cancel_order", "prepare_refund_confirmation"}
EXISTING_REFUND_STATUSES = {"pending", "withdrawn", "completed"}
# 完成一次高风险写入后，恢复查询应返回的历史结果状态。
RECOVERY_RESULT_STATUS_BY_OUTCOME = {
    "canceled": "canceled",
    "refund_submitted": "submitted",
    "refund_withdrawn": "withdrawn",
}
CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    user_id: str
    user_message: str
    order: dict[str, Any]
    tool_call: dict[str, Any]
    confirmation: dict[str, Any]
    expected: dict[str, Any]
    existing_refund: dict[str, Any] | None = None
    fault: FaultConfig | None = None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _parse_case(data: dict) -> EvalCase:
    case_id = data.get("case_id")
    user_id = data.get("user_id")
    user_message = data.get("user_message")
    _require(isinstance(case_id, str) and bool(case_id.strip()), "case_id 不能为空")
    _require(
        CASE_ID_PATTERN.fullmatch(case_id) is not None,
        f"case_id 只能包含字母、数字、下划线和连字符: {case_id}",
    )
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
        order["status"] in {"unpaid", "paid", "shipped"},
        f"{case_id}: order.status 只支持 unpaid、paid 或 shipped",
    )
    _require(
        type(order["paid_amount_cents"]) is int and order["paid_amount_cents"] >= 0,
        f"{case_id}: order.paid_amount_cents 必须是非负整数",
    )
    if order["status"] == "unpaid":
        _require(
            order["paid_amount_cents"] == 0,
            f"{case_id}: 未付款订单的实付金额必须为 0",
        )
    else:
        _require(
            order["paid_amount_cents"] > 0,
            f"{case_id}: 已付款订单的实付金额必须是正整数",
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
    tool_name = tool_call.get("name")
    _require(
        tool_name in WRITE_TOOLS,
        f"{case_id}: 工具只支持 cancel_order、prepare_refund_confirmation 或 withdraw_refund",
    )
    arguments = tool_call.get("arguments")
    _require(isinstance(arguments, dict), f"{case_id}: tool_call.arguments 必须是对象")
    _require(
        arguments.get("order_id") == order["order_id"],
        f"{case_id}: 工具参数 order_id 与用例订单不一致",
    )
    if tool_name in REASON_TOOLS:
        _require(
            isinstance(arguments.get("reason"), str) and bool(arguments["reason"].strip()),
            f"{case_id}: 工具参数 reason 不能为空",
        )
    else:
        _require(
            set(arguments) == {"order_id"},
            f"{case_id}: withdraw_refund 只接受 order_id",
        )

    existing = data.get("existing_refund")
    if existing is None:
        normalized_existing = None
    else:
        _require(isinstance(existing, dict), f"{case_id}: existing_refund 必须是对象")
        _require(
            tool_name == "withdraw_refund",
            f"{case_id}: 只有撤回用例可以预置退款申请",
        )
        _require(
            order["status"] == "shipped",
            f"{case_id}: 预置售后申请要求订单为已发货",
        )
        existing_status = existing.get("status", "pending")
        _require(
            existing_status in EXISTING_REFUND_STATUSES,
            f"{case_id}: existing_refund.status 只支持 pending、withdrawn 或 completed",
        )
        existing_amount = existing.get("amount_cents", order["paid_amount_cents"])
        _require(
            type(existing_amount) is int and existing_amount == order["paid_amount_cents"],
            f"{case_id}: 预置申请金额必须等于订单实付金额",
        )
        existing_reason = existing.get("reason", "商品破损")
        _require(
            isinstance(existing_reason, str) and bool(existing_reason.strip()),
            f"{case_id}: existing_refund.reason 不能为空",
        )
        normalized_existing = {
            "status": existing_status,
            "kind": "after_sales",
            "amount_cents": existing_amount,
            "reason": existing_reason,
        }

    confirmation = data.get("confirmation", {"mode": "explicit", "decision": "approve"})
    _require(isinstance(confirmation, dict), f"{case_id}: confirmation 必须是对象")
    confirmation_mode = confirmation.get("mode", "explicit")
    _require(
        confirmation_mode in CONFIRMATION_MODES,
        f"{case_id}: confirmation.mode 只支持 explicit、chat 或 none",
    )
    if confirmation_mode == "explicit":
        decision = confirmation.get("decision", "approve")
        _require(decision in {"approve", "reject"}, f"{case_id}: confirmation.decision 不合法")
        normalized_confirmation = {"mode": "explicit", "decision": decision}
    elif confirmation_mode == "chat":
        confirmation_message = confirmation.get("message")
        intent = confirmation.get("intent")
        evidence = confirmation.get("evidence", confirmation_message)
        _require(
            isinstance(confirmation_message, str) and bool(confirmation_message.strip()),
            f"{case_id}: confirmation.message 不能为空",
        )
        _require(intent in {"approve", "reject"}, f"{case_id}: confirmation.intent 不合法")
        _require(
            isinstance(evidence, str) and bool(evidence.strip()),
            f"{case_id}: confirmation.evidence 不能为空",
        )
        _require(
            evidence in confirmation_message,
            f"{case_id}: confirmation.evidence 必须是 confirmation.message 的子串",
        )
        normalized_confirmation = {
            "mode": "chat",
            "message": confirmation_message,
            "intent": intent,
            "evidence": evidence,
        }
    else:
        normalized_confirmation = {"mode": "none"}

    try:
        fault = parse_fault(data.get("fault"))
    except ValueError as exc:
        raise ValueError(f"{case_id}: {exc}") from exc
    if fault is not None:
        _require(
            normalized_confirmation["mode"] != "none",
            f"{case_id}: 故障注入用例必须包含确认动作",
        )
        decided = normalized_confirmation.get("decision") or normalized_confirmation.get("intent")
        _require(
            decided == "approve",
            f"{case_id}: 故障注入用例只在批准路径上验证恢复",
        )

    expected = data.get("expected")
    _require(isinstance(expected, dict), f"{case_id}: expected 必须是对象")
    outcome = expected.get("outcome")
    _require(
        outcome in OUTCOMES,
        f"{case_id}: expected.outcome 必须是 canceled、rejected、refund_submitted 或 refund_withdrawn",
    )
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

    confirmation_decision = (
        normalized_confirmation.get("decision")
        if confirmation_mode == "explicit"
        else normalized_confirmation.get("intent")
    )

    if outcome == "canceled":
        _require(
            order["status"] in {"unpaid", "paid"},
            f"{case_id}: 成功取消用例必须是未付款或已付款订单",
        )
        _require(confirmation_mode != "none", f"{case_id}: 成功取消必须有确认动作")
        _require(confirmation_decision == "approve", f"{case_id}: 成功取消的确认意图必须是 approve")
        _require(expected["order_status"] == "canceled", f"{case_id}: 成功取消的最终状态必须是 canceled")
        _require(expected["confirmation_count"] == 1, f"{case_id}: 成功取消必须恰好有一条确认记录")
        _require(expected["operation_logs"] == 1, f"{case_id}: 成功取消必须恰好有一条操作日志")
        _require(
            expected.get("confirmation_status") == "consumed",
            f"{case_id}: 成功取消的确认必须已消费",
        )
        _require(
            expected.get("confirmation_result_status") == "canceled",
            f"{case_id}: 成功取消的确认结果必须是 canceled",
        )
        if order["status"] == "unpaid":
            _require(
                expected["refund_count"] == 0,
                f"{case_id}: 未付款订单取消不生成退款流水",
            )
        else:
            _require(expected["refund_count"] == 1, f"{case_id}: 成功取消必须恰好生成一条退款")
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
    elif outcome == "refund_submitted":
        _require(
            tool_name == "prepare_refund_confirmation",
            f"{case_id}: 退款提交用例必须使用 prepare_refund_confirmation",
        )
        _require(order["status"] == "shipped", f"{case_id}: 退款提交用例必须是已发货订单")
        _require(confirmation_mode != "none", f"{case_id}: 退款提交必须有确认动作")
        _require(
            confirmation_decision == "approve",
            f"{case_id}: 退款提交的确认意图必须是 approve",
        )
        _require(
            expected["order_status"] == order["status"],
            f"{case_id}: 提交退款申请不应改变订单状态",
        )
        _require(expected["refund_count"] == 1, f"{case_id}: 成功提交必须恰好生成一条退款申请")
        _require(expected["confirmation_count"] == 1, f"{case_id}: 成功提交必须恰好有一条确认记录")
        _require(expected["operation_logs"] == 0, f"{case_id}: 提交退款申请不应写取消日志")
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
            f"{case_id}: 退款提交的 refund_kind 必须是 after_sales",
        )
        _require(
            expected["refund_amount_cents"] == order["paid_amount_cents"],
            f"{case_id}: 退款金额必须等于订单实付金额",
        )
        _require(expected["refund_status"] == "pending", f"{case_id}: 新退款申请必须是 pending")
        _require(
            expected["confirmation_status"] == "consumed",
            f"{case_id}: 退款确认必须已消费",
        )
        _require(
            expected["confirmation_result_status"] == "submitted",
            f"{case_id}: 确认结果必须是 submitted",
        )
    elif outcome == "refund_withdrawn":
        _require(
            tool_name == "withdraw_refund",
            f"{case_id}: 撤回用例必须使用 withdraw_refund",
        )
        _require(
            normalized_existing is not None,
            f"{case_id}: 撤回用例必须预置一条退款申请",
        )
        _require(
            normalized_existing["status"] == "pending",
            f"{case_id}: 只有 pending 申请可以撤回",
        )
        _require(confirmation_mode != "none", f"{case_id}: 撤回必须有确认动作")
        _require(
            confirmation_decision == "approve",
            f"{case_id}: 撤回的确认意图必须是 approve",
        )
        _require(
            expected["order_status"] == order["status"],
            f"{case_id}: 撤回不应改变订单状态",
        )
        _require(expected["refund_count"] == 1, f"{case_id}: 撤回后仍需保留原申请")
        _require(
            expected.get("refund_kind") == "after_sales",
            f"{case_id}: 撤回用例的 refund_kind 必须是 after_sales",
        )
        _require(
            expected.get("refund_status") == "withdrawn",
            f"{case_id}: 撤回后申请状态必须是 withdrawn",
        )
        _require(
            expected.get("refund_amount_cents") == order["paid_amount_cents"],
            f"{case_id}: 撤回不应改变申请金额",
        )
        _require(expected["confirmation_count"] == 1, f"{case_id}: 撤回必须恰好有一条确认记录")
        _require(
            expected.get("confirmation_status") == "consumed",
            f"{case_id}: 撤回确认必须已消费",
        )
        _require(
            expected.get("confirmation_result_status") == "withdrawn",
            f"{case_id}: 确认结果必须是 withdrawn",
        )
        _require(
            expected.get("confirmation_tool", "cancel_refund") == "cancel_refund",
            f"{case_id}: 撤回确认记录的工具必须是 cancel_refund",
        )
        _require(expected["operation_logs"] == 0, f"{case_id}: 撤回不应写取消日志")
    else:
        _require(
            expected["order_status"] == order["status"],
            f"{case_id}: 拒绝用例的最终状态应与初始订单状态一致",
        )
        if normalized_existing is None:
            _require(expected["refund_count"] == 0, f"{case_id}: 拒绝用例不能生成退款")
        else:
            _require(
                expected["refund_count"] == 1,
                f"{case_id}: 预置申请在拒绝后必须原样保留",
            )
            _require(
                expected.get("refund_kind") == "after_sales",
                f"{case_id}: 预置申请的 refund_kind 必须是 after_sales",
            )
            _require(
                expected.get("refund_status") == normalized_existing["status"],
                f"{case_id}: 拒绝后申请状态不应改变",
            )
            _require(
                expected.get("refund_amount_cents") == normalized_existing["amount_cents"],
                f"{case_id}: 拒绝后申请金额不应改变",
            )
        _require(expected["operation_logs"] == 0, f"{case_id}: 拒绝用例不能写操作日志")
        if confirmation_mode == "none":
            _require(
                expected["confirmation_count"] == 0,
                f"{case_id}: 没有确认动作时不能生成确认记录",
            )
        else:
            _require(
                confirmation_decision == "reject",
                f"{case_id}: 拒绝用例的确认意图必须是 reject",
            )
            _require(
                expected["confirmation_count"] == 1,
                f"{case_id}: 用户拒绝后必须恰好有一条确认记录",
            )
            _require(
                expected.get("confirmation_status") == "rejected",
                f"{case_id}: 用户拒绝后确认状态必须是 rejected",
            )

    if fault is None:
        _require(
            "recovery_state" not in expected and "recovery_result_status" not in expected,
            f"{case_id}: 没有故障注入时不应声明恢复期望",
        )
    else:
        _require(
            expected.get("recovery_state") == "executed",
            f"{case_id}: 故障注入用例必须声明 recovery_state 为 executed",
        )
        _require(
            expected.get("recovery_result_status") == RECOVERY_RESULT_STATUS_BY_OUTCOME[outcome],
            f"{case_id}: recovery_result_status 必须是 {RECOVERY_RESULT_STATUS_BY_OUTCOME[outcome]}",
        )

    normalized_expected = dict(expected)
    normalized_expected["other_orders_canceled"] = other_orders

    return EvalCase(
        case_id=case_id,
        user_id=user_id,
        user_message=user_message,
        order=order,
        tool_call=tool_call,
        confirmation=normalized_confirmation,
        expected=normalized_expected,
        existing_refund=normalized_existing,
        fault=fault,
    )


def _read_case_data(path: Path) -> Iterator[tuple[int, dict]]:
    """逐行读取 JSONL；空行跳过，非法行给出明确位置。"""
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


def load_case(case_id: str, path: Path | None = None) -> EvalCase:
    """按 case_id 读取一条用例；文件缺失或内容不合法时抛出 ValueError。"""
    cases_path = path if path is not None else DEFAULT_CASES_PATH
    for _, data in _read_case_data(cases_path):
        if data.get("case_id") == case_id:
            return _parse_case(data)
    raise ValueError(f"未找到用例 {case_id}（文件: {cases_path}）")


def load_cases(path: Path | None = None) -> list[EvalCase]:
    """读取并校验全部用例；用于批量运行。"""
    cases_path = path if path is not None else DEFAULT_CASES_PATH
    cases: list[EvalCase] = []
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