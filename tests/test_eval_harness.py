"""最小评测闭环：成功路径、拒绝路径、Oracle 负向判定和 CLI 产物。"""

import json
import sys
from dataclasses import replace

import pytest

from app.storage import database
from eval import run_eval
from eval.case_schema import DEFAULT_CASES_PATH, load_case, load_cases
from eval.fixtures import FIXED_NOW, isolated_database, seed_case_order
from eval.harness import run_case
from eval.oracle import evaluate
from eval.reporter import build_summary


def read_events(path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_cancel_paid_order_case_passes(tmp_path):
    case = load_case("cancel_paid_order")

    result = run_case(case, trace_path=tmp_path / "trace.jsonl")

    assert result["passed"] is True, result
    assert result["outcome"] == "canceled"
    assert result["confirmation_mode"] == "explicit"
    assert all(check["ok"] for check in result["checks"])
    assert result["pending_action"] is not None
    assert "confirmation_id" not in result["pending_action"]
    assert (tmp_path / "trace.jsonl").exists()


def test_cancel_paid_order_chat_confirmation_passes(tmp_path):
    case = load_case("cancel_paid_order_chat_confirm")

    result = run_case(case, trace_path=tmp_path / "trace.jsonl")

    assert result["passed"] is True, result
    assert result["outcome"] == "canceled"
    assert result["confirmation_mode"] == "chat"
    assert result["pending_action"] is not None
    assert result["remaining_pending_action"] is None
    assert all(check["ok"] for check in result["checks"])

    events = read_events(tmp_path / "trace.jsonl")
    intents = [event for event in events if event["event"] == "confirmation.intent"]
    decisions = [event for event in events if event["event"] == "confirmation.decision"]
    assert intents[-1]["intent"] == "approve"
    assert decisions[-1]["decision"] == "approve"
    assert decisions[-1]["source"] == "chat"


def test_refund_shipped_order_chat_confirmation_passes(tmp_path):
    case = load_case("refund_shipped_order_chat_confirm")

    result = run_case(case, trace_path=tmp_path / "trace.jsonl")

    assert result["passed"] is True, result
    assert result["outcome"] == "refund_submitted"
    assert result["confirmation_mode"] == "chat"
    assert result["pending_action"] is not None
    assert result["remaining_pending_action"] is None
    assert all(check["ok"] for check in result["checks"])

    checks = {check["name"]: check["actual"] for check in result["checks"]}
    assert checks["order.status"] == "shipped"
    assert checks["refund.cancellation_count"] == 0
    assert checks["refund.after_sales_count"] == 1
    assert checks["refund.kind"] == "after_sales"
    assert checks["refund.status"] == "pending"
    assert checks["confirmation.status"] == "consumed"
    assert checks["confirmation.result_status"] == "submitted"
    assert checks["operation_logs.count"] == 0

    events = read_events(tmp_path / "trace.jsonl")
    intents = [event for event in events if event["event"] == "confirmation.intent"]
    decisions = [event for event in events if event["event"] == "confirmation.decision"]
    assert intents[-1]["intent"] == "approve"
    assert decisions[-1]["decision"] == "approve"
    assert decisions[-1]["source"] == "chat"


def test_refund_shipped_order_reject_case_passes(tmp_path):
    case = load_case("refund_shipped_order_reject")

    result = run_case(case, trace_path=tmp_path / "trace.jsonl")

    assert result["passed"] is True, result
    assert result["outcome"] == "rejected"
    assert result["confirmation_mode"] == "chat"
    assert result["pending_action"] is not None
    assert result["remaining_pending_action"] is None
    assert all(check["ok"] for check in result["checks"])

    checks = {check["name"]: check["actual"] for check in result["checks"]}
    assert checks["order.status"] == "shipped"
    assert checks["refund.cancellation_count"] == 0
    assert checks["refund.after_sales_count"] == 0
    assert checks["confirmation.count"] == 1
    assert checks["confirmation.status"] == "rejected"
    assert checks["operation_logs.count"] == 0

    events = read_events(tmp_path / "trace.jsonl")
    intents = [event for event in events if event["event"] == "confirmation.intent"]
    decisions = [event for event in events if event["event"] == "confirmation.decision"]
    assert intents[-1]["intent"] == "reject"
    assert decisions[-1]["decision"] == "reject"
    assert decisions[-1]["source"] == "chat"


def test_cancel_shipped_order_rejection_case_passes(tmp_path):
    case = load_case("cancel_shipped_order")

    result = run_case(case, trace_path=tmp_path / "trace.jsonl")

    assert result["passed"] is True, result
    assert result["outcome"] == "rejected"
    assert result["confirmation_mode"] == "none"
    assert result["pending_action"] is None
    assert all(check["ok"] for check in result["checks"])

    tool_events = [
        event for event in read_events(tmp_path / "trace.jsonl") if event["event"] == "tool.call"
    ]
    assert len(tool_events) == 1
    assert tool_events[0]["tool"] == "cancel_order"
    assert tool_events[0]["error"] == "POLICY_DENIED"


def test_oracle_fails_when_cancellation_did_not_happen(tmp_path):
    case = load_case("cancel_paid_order")
    db_path = tmp_path / "retail.db"

    with isolated_database(db_path):
        seed_case_order(case)
        result = evaluate(db_path, case)

    assert result["passed"] is False
    failed = {check["name"] for check in result["checks"] if not check["ok"]}
    assert {"order.status", "refund.cancellation_count"} <= failed


def test_oracle_fails_when_rejected_case_has_refund(tmp_path):
    case = load_case("cancel_shipped_order")
    db_path = tmp_path / "retail.db"

    with isolated_database(db_path):
        seed_case_order(case)
        conn = database.get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO refund_requests (
                        refund_id, order_id, user_id, kind, amount_cents, reason, status,
                        idempotency_key, confirmation_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "rf_bad",
                        case.order["order_id"],
                        case.user_id,
                        "cancellation",
                        case.order["paid_amount_cents"],
                        "测试写入",
                        "completed",
                        "bad-key",
                        None,
                        FIXED_NOW.isoformat(),
                        FIXED_NOW.isoformat(),
                    ),
                )
        finally:
            conn.close()
        result = evaluate(db_path, case)

    assert result["passed"] is False
    failed = {check["name"] for check in result["checks"] if not check["ok"]}
    assert "refund.cancellation_count" in failed


def test_oracle_fails_when_refund_submission_did_not_happen(tmp_path):
    case = load_case("refund_shipped_order_chat_confirm")
    db_path = tmp_path / "retail.db"

    with isolated_database(db_path):
        seed_case_order(case)
        result = evaluate(db_path, case)

    assert result["passed"] is False
    failed = {check["name"] for check in result["checks"] if not check["ok"]}
    assert {"refund.after_sales_count", "confirmation.count"} <= failed


def test_oracle_fails_when_rejected_refund_has_after_sales(tmp_path):
    case = load_case("refund_shipped_order_reject")
    db_path = tmp_path / "retail.db"

    with isolated_database(db_path):
        seed_case_order(case)
        conn = database.get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO refund_requests (
                        refund_id, order_id, user_id, kind, amount_cents, reason, status,
                        idempotency_key, confirmation_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "rf_bad_after_sales",
                        case.order["order_id"],
                        case.user_id,
                        "after_sales",
                        case.order["paid_amount_cents"],
                        "测试写入",
                        "pending",
                        "bad-after-sales-key",
                        None,
                        FIXED_NOW.isoformat(),
                        FIXED_NOW.isoformat(),
                    ),
                )
        finally:
            conn.close()
        result = evaluate(db_path, case)

    assert result["passed"] is False
    failed = {check["name"] for check in result["checks"] if not check["ok"]}
    assert "refund.after_sales_count" in failed


@pytest.mark.parametrize(
    "case_id",
    [
        "cancel_paid_order",
        "cancel_shipped_order",
        "cancel_paid_order_chat_confirm",
        "refund_shipped_order_chat_confirm",
        "refund_shipped_order_reject",
    ],
)
def test_cli_writes_result_and_trace(case_id, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["retail-eval", "--case", case_id, "--output", str(tmp_path)],
    )

    exit_code = run_eval.main()

    assert exit_code == 0
    assert f"PASS {case_id}" in capsys.readouterr().out

    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["case_id"] == case_id
    assert result["passed"] is True
    assert (tmp_path / "trace.jsonl").exists()


def test_cli_all_runs_every_case(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["retail-eval", "--all", "--output", str(tmp_path)],
    )
    # 用例数量随开发集增长，断言从文件读取，避免每加一条用例就改测试。
    case_ids = {case.case_id for case in load_cases()}

    exit_code = run_eval.main()

    assert exit_code == 0
    output = capsys.readouterr().out
    for case_id in case_ids:
        assert f"PASS {case_id}" in output
        assert (tmp_path / case_id / "result.json").exists()
        assert (tmp_path / case_id / "trace.jsonl").exists()
    total = len(case_ids)
    assert f"summary: {total}/{total} passed" in output

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["total"] == total
    assert summary["passed"] == total
    assert summary["failed"] == 0
    assert summary["duration_ms"] >= 0
    assert {case["case_id"] for case in summary["cases"]} == case_ids
    assert all(case["failed_checks"] == [] for case in summary["cases"])


def test_summary_counts_failures():
    summary = build_summary(
        [
            {
                "case_id": "pass_case",
                "passed": True,
                "duration_ms": 10.0,
                "checks": [],
            },
            {
                "case_id": "fail_case",
                "passed": False,
                "duration_ms": 20.0,
                "checks": [
                    {
                        "name": "order.status",
                        "ok": False,
                        "actual": "paid",
                        "expected": "canceled",
                    }
                ],
                "error": "执行失败",
            },
        ],
        duration_ms=31.234,
    )

    assert summary["total"] == 2
    assert summary["passed"] == 1
    assert summary["failed"] == 1
    assert summary["duration_ms"] == 31.23
    assert summary["cases"][1]["failed_checks"] == ["order.status"]
    assert summary["cases"][1]["error"] == "执行失败"


def test_withdraw_pending_refund_passes(tmp_path):
    case = load_case("withdraw_pending_refund")

    result = run_case(case, trace_path=tmp_path / "trace.jsonl")

    assert result["passed"] is True, result
    assert result["checks"]
    assert all(check["ok"] for check in result["checks"])


def test_withdraw_already_withdrawn_rejected_without_writing():
    case = load_case("withdraw_already_withdrawn")

    result = run_case(case)

    assert result["passed"] is True, result
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["refund.after_sales_count"]["actual"] == 1
    assert checks["refund.status"]["actual"] == "withdrawn"
    assert checks["confirmation.count"]["actual"] == 0


def test_oracle_detects_wrong_withdraw_expectation():
    """把期望状态改错后必须失败，证明 Oracle 不是只复述 Agent 的回复。"""
    case = load_case("withdraw_pending_refund")
    broken = replace(case, expected={**case.expected, "refund_status": "pending"})

    result = run_case(broken)

    assert result["passed"] is False
    failed = {
        check["name"] for check in result["checks"] if not check["ok"]
    }
    assert "refund.status" in failed


def test_oracle_detects_wrong_order_status_expectation():
    case = load_case("withdraw_pending_refund")
    broken = replace(case, expected={**case.expected, "order_status": "refunded"})

    result = run_case(broken)

    assert result["passed"] is False
    failed = {
        check["name"] for check in result["checks"] if not check["ok"]
    }
    assert "order.status" in failed


def _write_single_case(tmp_path, case_id: str, mutate) -> object:
    """把开发集中的一条用例改坏后写入临时文件，返回路径。"""
    for line in DEFAULT_CASES_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        if data.get("case_id") == case_id:
            mutate(data)
            path = tmp_path / "cases.jsonl"
            path.write_text(
                json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8",
            )
            return path
    raise AssertionError(f"开发集没有用例 {case_id}")


def test_schema_rejects_reason_on_withdraw(tmp_path):
    path = _write_single_case(
        tmp_path,
        "withdraw_pending_refund",
        lambda data: data["tool_call"]["arguments"].update({"reason": "不想要了"}),
    )

    with pytest.raises(ValueError, match="withdraw_refund 只接受 order_id"):
        load_case("withdraw_pending_refund", path)


def test_schema_rejects_existing_refund_on_cancel(tmp_path):
    def mutate(data):
        data["case_id"] = "cancel_with_existing"
        data["tool_call"] = {
            "name": "cancel_order",
            "arguments": {
                "order_id": data["order"]["order_id"],
                "reason": "不想要了",
            },
        }
        data["existing_refund"] = {"status": "pending"}

    path = _write_single_case(tmp_path, "withdraw_pending_refund", mutate)

    with pytest.raises(ValueError, match="只有撤回用例可以预置退款申请"):
        load_case("cancel_with_existing", path)


def test_schema_rejects_unpaid_with_positive_amount(tmp_path):
    path = _write_single_case(
        tmp_path,
        "cancel_unpaid_order",
        lambda data: data["order"].update({"paid_amount_cents": 9900}),
    )

    with pytest.raises(ValueError, match="未付款订单的实付金额必须为 0"):
        load_case("cancel_unpaid_order", path)