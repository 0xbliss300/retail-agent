"""最小评测闭环：正向用例、Oracle 负向判定和 CLI 产物。"""

import json
import sys

from eval import run_eval
from eval.case_schema import load_case
from eval.fixtures import isolated_database, seed_case_order
from eval.harness import run_case
from eval.oracle import evaluate


def test_cancel_paid_order_case_passes(tmp_path):
    case = load_case("cancel_paid_order")

    result = run_case(case, trace_path=tmp_path / "trace.jsonl")

    assert result["passed"] is True, result
    assert all(check["ok"] for check in result["checks"])
    assert "confirmation_id" not in result["pending_action"]
    assert (tmp_path / "trace.jsonl").exists()


def test_oracle_fails_when_cancellation_did_not_happen(tmp_path):
    case = load_case("cancel_paid_order")
    db_path = tmp_path / "retail.db"

    with isolated_database(db_path):
        seed_case_order(case)
        result = evaluate(db_path, case)

    assert result["passed"] is False
    failed = {check["name"] for check in result["checks"] if not check["ok"]}
    assert {"order.status", "refund.count"} <= failed


def test_cli_writes_result_and_trace(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["retail-eval", "--case", "cancel_paid_order", "--output", str(tmp_path)],
    )

    exit_code = run_eval.main()

    assert exit_code == 0
    assert "PASS cancel_paid_order" in capsys.readouterr().out

    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["passed"] is True
    assert (tmp_path / "trace.jsonl").exists()