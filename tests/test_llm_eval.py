"""真实 LLM 冒烟评测的离线管道测试，使用假客户端避免真实网络。"""

import json
import sys

from openai.types.chat import ChatCompletionMessage

from eval import run_eval
from eval.harness import ScriptedClient
from eval.llm_case_schema import load_llm_case
from eval.llm_harness import run_llm_case
from eval.metrics import build_multi_case_summary, build_run_summary


def tool_call_message(name: str, arguments: dict, call_id: str) -> ChatCompletionMessage:
    return ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        ],
    )


def fake_client() -> ScriptedClient:
    return ScriptedClient(
        [
            tool_call_message(
                "cancel_order",
                {"order_id": "ORD-EVAL-4", "reason": "不想要了"},
                "call_cancel",
            ),
            tool_call_message(
                "report_confirmation_intent",
                {"intent": "approve", "evidence": "确认取消"},
                "call_intent",
            ),
        ]
    )


def order_query_client() -> ScriptedClient:
    return ScriptedClient(
        [
            tool_call_message("list_orders", {}, "call_list"),
            ChatCompletionMessage(role="assistant", content="你有两个订单。"),
        ]
    )


def order_detail_client() -> ScriptedClient:
    return ScriptedClient(
        [
            tool_call_message("get_order", {"order_id": "ORD-EVAL-11"}, "call_get"),
            ChatCompletionMessage(role="assistant", content="订单 ORD-EVAL-11 已付款。"),
        ]
    )


def policy_query_client() -> ScriptedClient:
    return ScriptedClient(
        [
            tool_call_message("get_policy", {"topic": "refund"}, "call_policy"),
            ChatCompletionMessage(role="assistant", content="退款需要满足售后条件。"),
        ]
    )


def refund_eligibility_client() -> ScriptedClient:
    return ScriptedClient(
        [
            tool_call_message(
                "check_refund_eligibility",
                {"order_id": "ORD-EVAL-9"},
                "call_eligibility",
            ),
            ChatCompletionMessage(role="assistant", content="该订单可以申请退款。"),
        ]
    )


def cross_user_client() -> ScriptedClient:
    return ScriptedClient(
        [
            tool_call_message("get_order", {"order_id": "ORD-1002"}, "call_other"),
            ChatCompletionMessage(role="assistant", content="未找到该订单。"),
        ]
    )


def shipped_rejection_client() -> ScriptedClient:
    return ScriptedClient(
        [
            tool_call_message(
                "cancel_order",
                {"order_id": "ORD-EVAL-6", "reason": "不想要了"},
                "call_cancel",
            ),
            ChatCompletionMessage(role="assistant", content="该订单已发货，不能取消。"),
        ]
    )


def cancel_reject_client() -> ScriptedClient:
    return ScriptedClient(
        [
            tool_call_message(
                "cancel_order",
                {"order_id": "ORD-EVAL-7", "reason": "不想要了"},
                "call_cancel",
            ),
            tool_call_message(
                "report_confirmation_intent",
                {"intent": "reject", "evidence": "算了，不取消了"},
                "call_intent",
            ),
        ]
    )


def test_run_llm_case_with_fake_client(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    case = load_llm_case("llm_cancel_paid_order")

    result = run_llm_case(
        case,
        fake_client(),
        trace_path=tmp_path / "trace.jsonl",
    )

    assert result["passed"] is True, result
    assert result["model"] == "fake-model"
    assert result["pending_action"] is not None
    assert result["remaining_pending_action"] is None
    assert all(check["ok"] for check in result["checks"])

    events = [
        json.loads(line)
        for line in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert any(
        event["event"] == "tool.call"
        and event["tool"] == "cancel_order"
        and event["ok"] is True
        for event in events
    )
    assert any(
        event["event"] == "confirmation.intent" and event["intent"] == "approve"
        for event in events
    )


def test_cli_llm_uses_client_factory(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    monkeypatch.setattr(
        "app.agent.model_client.create_client",
        lambda: fake_client(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "retail-eval",
            "--llm",
            "--case",
            "llm_cancel_paid_order",
            "--output",
            str(tmp_path),
        ],
    )

    exit_code = run_eval.main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "PASS llm_cancel_paid_order" in output
    assert "model: fake-model" in output

    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["passed"] is True
    assert (tmp_path / "trace.jsonl").exists()


def test_build_run_summary_counts_failures():
    summary = build_run_summary(
        [
            {
                "case_id": "llm_cancel_paid_order",
                "passed": True,
                "duration_ms": 100.0,
                "checks": [],
            },
            {
                "case_id": "llm_cancel_paid_order",
                "passed": False,
                "duration_ms": 200.0,
                "checks": [
                    {
                        "name": "order.status",
                        "ok": False,
                        "actual": "paid",
                        "expected": "canceled",
                    }
                ],
                "error": None,
            },
            {
                "case_id": "llm_cancel_paid_order",
                "passed": True,
                "duration_ms": 300.0,
                "checks": [],
            },
        ],
        model="fake-model",
    )

    assert summary["case_id"] == "llm_cancel_paid_order"
    assert summary["model"] == "fake-model"
    assert summary["runs"] == 3
    assert summary["passed"] == 2
    assert summary["failed"] == 1
    assert summary["success_rate"] == 0.6667
    assert summary["avg_duration_ms"] == 200.0
    assert summary["runs_detail"][1]["failed_checks"] == ["order.status"]
    assert summary["runs_detail"][1]["error"] is None


def test_cli_llm_runs_multiple_times(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    monkeypatch.setattr(
        "app.agent.model_client.create_client",
        lambda: fake_client(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "retail-eval",
            "--llm",
            "--case",
            "llm_cancel_paid_order",
            "--runs",
            "3",
            "--output",
            str(tmp_path),
        ],
    )

    exit_code = run_eval.main()

    assert exit_code == 0
    output = capsys.readouterr().out
    for index in range(1, 4):
        assert f"run {index}/3: PASS" in output
        assert (tmp_path / f"run-{index:02d}" / "result.json").exists()
        assert (tmp_path / f"run-{index:02d}" / "trace.jsonl").exists()
    assert "summary: 3/3 passed (100.0%)" in output
    assert "avg_duration_ms:" in output

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["runs"] == 3
    assert summary["passed"] == 3
    assert summary["failed"] == 0
    assert summary["success_rate"] == 1.0
    assert summary["model"] == "fake-model"
    assert len(summary["runs_detail"]) == 3


def test_cli_llm_all_runs_cases(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    clients = iter(
        [
            order_query_client(),
            order_detail_client(),
            policy_query_client(),
            refund_eligibility_client(),
            fake_client(),
            shipped_rejection_client(),
            cancel_reject_client(),
            cross_user_client(),
        ]
    )
    monkeypatch.setattr(
        "app.agent.model_client.create_client",
        lambda: next(clients),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "retail-eval",
            "--llm",
            "--all",
            "--runs",
            "1",
            "--output",
            str(tmp_path),
        ],
    )
    case_ids = {
        "llm_order_query",
        "llm_order_detail",
        "llm_policy_query",
        "llm_refund_eligibility",
        "llm_cancel_paid_order",
        "llm_cancel_shipped_order",
        "llm_cancel_reject",
        "llm_cross_user_order",
    }

    exit_code = run_eval.main()

    assert exit_code == 0
    output = capsys.readouterr().out
    for case_id in case_ids:
        assert f"{case_id}: PASS" in output
        assert (tmp_path / case_id / "result.json").exists()
        assert (tmp_path / case_id / "trace.jsonl").exists()
        assert (tmp_path / case_id / "summary.json").exists()

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["cases"] == 8
    assert summary["runs_per_case"] == 1
    assert summary["total_runs"] == 8
    assert summary["passed"] == 8
    assert summary["failed"] == 0
    assert summary["accuracy"] == 1.0
    assert summary["pass@1"] == 1.0
    assert summary["pass_at_k"] == 1.0
    assert summary["policy_violation_rate"] == 0.0
    assert summary["p50_latency_ms"] >= 0
    assert summary["p95_latency_ms"] >= 0
    assert len(summary["cases_detail"]) == 8


def test_build_multi_case_summary_metrics():
    summary = build_multi_case_summary(
        [
            {
                "case_id": "case_a",
                "model": "fake-model",
                "runs": 3,
                "passed": 2,
                "policy_violations": 0,
                "runs_detail": [
                    {"duration_ms": 100.0},
                    {"duration_ms": 110.0},
                    {"duration_ms": 120.0},
                ],
            },
            {
                "case_id": "case_b",
                "model": "fake-model",
                "runs": 3,
                "passed": 0,
                "policy_violations": 1,
                "runs_detail": [
                    {"duration_ms": 200.0},
                    {"duration_ms": 210.0},
                    {"duration_ms": 220.0},
                ],
            },
        ],
        model="fake-model",
        runs_per_case=3,
    )

    assert summary["cases"] == 2
    assert summary["runs_per_case"] == 3
    assert summary["total_runs"] == 6
    assert summary["passed"] == 2
    assert summary["failed"] == 4
    assert summary["accuracy"] == 0.3333
    assert summary["pass@1"] == 0.3333
    assert summary["pass_at_k"] == 0.5
    assert summary["k"] == 3
    assert summary["policy_violation_rate"] == 0.1667
    assert summary["avg_duration_ms"] == 160.0
    assert summary["p50_latency_ms"] == 160.0
    assert summary["p95_latency_ms"] == 217.5