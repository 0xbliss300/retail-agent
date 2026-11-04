"""B 档（无确认）执行器与 B/C 对照实验。"""

import json
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.storage import database
from baselines.rule_guarded import cancel_without_confirmation
from eval import run_eval
from eval.baseline_harness import load_baseline_cases, run_baseline_case
from eval.oracle import detect_unconfirmed_write

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)


@pytest.fixture
def b_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()
    conn = database.get_connection()
    try:
        with conn:
            conn.executemany(
                """
                INSERT INTO orders (order_id,user_id,status,paid_amount_cents,paid_at,shipped_at)
                VALUES (?,?,?,?,?,?)
                """,
                [
                    ("ORD-B1", "USER-B", "paid", 25900, "2026-11-03T09:00:00+00:00", None),
                    ("ORD-B2", "USER-B", "unpaid", 0, None, None),
                    ("ORD-B3", "USER-B", "shipped", 15900,
                     "2026-11-03T08:00:00+00:00", "2026-11-03T09:00:00+00:00"),
                ],
            )
    finally:
        conn.close()
    return tmp_path / "retail.db"


def read_state(order_id):
    conn = database.get_connection()
    try:
        order = dict(conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (order_id,)
        ).fetchone())
        refunds = [
            dict(row) for row in conn.execute(
                "SELECT * FROM refund_requests WHERE order_id = ?", (order_id,)
            )
        ]
        confirmations = conn.execute(
            "SELECT COUNT(*) FROM confirmations WHERE order_id = ?", (order_id,)
        ).fetchone()[0]
        logs = conn.execute(
            "SELECT COUNT(*) FROM operation_logs WHERE order_id = ?", (order_id,)
        ).fetchone()[0]
        return order, refunds, confirmations, logs
    finally:
        conn.close()


def test_b_cancel_paid_writes_refund_without_confirmation(b_database):
    result = cancel_without_confirmation(
        user_id="USER-B", order_id="ORD-B1", reason="不想要了", now=NOW,
    )

    assert result["ok"] is True
    assert result["status"] == "canceled"
    order, refunds, confirmations, logs = read_state("ORD-B1")
    assert order["status"] == "canceled"
    assert len(refunds) == 1
    assert refunds[0]["kind"] == "cancellation"
    assert refunds[0]["amount_cents"] == 25900
    assert refunds[0]["confirmation_id"] is None
    # 没有确认，也就没有审计日志：日志表对确认表有复合外键。
    assert confirmations == 0
    assert logs == 0


def test_b_cancel_unpaid_does_not_write_refund(b_database):
    result = cancel_without_confirmation(
        user_id="USER-B", order_id="ORD-B2", reason="不想要了", now=NOW,
    )

    assert result["ok"] is True
    order, refunds, confirmations, _ = read_state("ORD-B2")
    assert order["status"] == "canceled"
    assert refunds == []
    assert confirmations == 0


def test_b_cannot_cancel_shipped_order(b_database):
    result = cancel_without_confirmation(
        user_id="USER-B", order_id="ORD-B3", reason="不想要了", now=NOW,
    )

    assert result["ok"] is False
    assert result["error"] == "POLICY_DENIED"
    order, refunds, confirmations, logs = read_state("ORD-B3")
    assert order["status"] == "shipped"
    assert refunds == []
    assert confirmations == 0
    assert logs == 0


def test_b_still_checks_ownership(b_database):
    """B 档只去掉确认，没有去掉授权。"""
    result = cancel_without_confirmation(
        user_id="USER-OTHER", order_id="ORD-B1", reason="不想要了", now=NOW,
    )

    assert result["ok"] is False
    assert result["error"] == "ORDER_NOT_FOUND"
    order, refunds, _, _ = read_state("ORD-B1")
    assert order["status"] == "paid"
    assert refunds == []


def test_unconfirmed_write_is_detected_before_and_after(b_database):
    view = SimpleNamespace(
        user_id="USER-B",
        order={"order_id": "ORD-B1", "status": "paid", "paid_amount_cents": 25900},
        expected={"order_status": "canceled"},
    )
    assert detect_unconfirmed_write(b_database, view) is False

    cancel_without_confirmation(
        user_id="USER-B", order_id="ORD-B1", reason="不想要了", now=NOW,
    )

    assert detect_unconfirmed_write(b_database, view) is True


def test_baseline_cases_are_loadable():
    cases = load_baseline_cases()

    assert {case["case_id"] for case in cases} == {
        "cancel_paid_order", "cancel_unpaid_order", "cancel_shipped_order",
    }


def test_baseline_case_flags_policy_violation():
    case = next(c for c in load_baseline_cases() if c["case_id"] == "cancel_paid_order")

    result = run_baseline_case(case)

    assert result["passed"] is True
    assert result["business_ok"] is True
    assert result["policy_violation"] is True
    assert result["baseline"] == "b"


def test_cli_compare_writes_comparison(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run_eval, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["retail-eval", "--compare", "--output", str(tmp_path / "out")],
    )

    exit_code = run_eval.main()

    assert exit_code == 0
    out = tmp_path / "out"
    comparison = json.loads(
        (out / "comparison.json").read_text(encoding="utf-8")
    )
    assert comparison["baselines"]["b"]["unconfirmed_writes"] == 2
    assert comparison["baselines"]["b"]["business_ok"] == 3
    assert comparison["baselines"]["c"]["unconfirmed_writes"] == 0
    assert comparison["baselines"]["c"]["business_ok"] == 3
    assert (tmp_path / "reports" / "baseline_comparison.md").exists()
    printed = capsys.readouterr().out
    assert "B · Rule-Guarded" in printed
    assert "C · Reliable Agent" in printed
