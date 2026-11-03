"""最小评测闭环：成功路径、拒绝路径、Oracle 负向判定和 CLI 产物。"""

import json
import sys

import pytest

from app.storage import database
from eval import run_eval
from eval.case_schema import load_case
from eval.fixtures import FIXED_NOW, isolated_database, seed_case_order
from eval.harness import run_case
from eval.oracle import evaluate


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
    assert all(check["ok"] for check in result["checks"])
    assert result["pending_action"] is not None
    assert "confirmation_id" not in result["pending_action"]
    assert (tmp_path / "trace.jsonl").exists()


def test_cancel_shipped_order_rejection_case_passes(tmp_path):
    case = load_case("cancel_shipped_order")

    result = run_case(case, trace_path=tmp_path / "trace.jsonl")

    assert result["passed"] is True, result
    assert result["outcome"] == "rejected"
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
    assert {"order.status", "refund.count"} <= failed


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
    assert "refund.count" in failed


@pytest.mark.parametrize("case_id", ["cancel_paid_order", "cancel_shipped_order"])
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