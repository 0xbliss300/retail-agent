"""真实 LLM 评测：模型自己选工具，Oracle + Trace 断言。"""

import json
import os
import tempfile
from pathlib import Path
from time import monotonic

from app.harness.conversation import new_conversation, send_message
from eval.fixtures import FIXED_NOW, isolated_database, seed_order
from eval.harness import sanitize_pending
from eval.llm_case_schema import LLMEvalCase
from eval.oracle import evaluate


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def _elapsed_ms(started: float) -> float:
    return round((monotonic() - started) * 1000, 2)


def _read_trace_events(trace_path: Path | None) -> list[dict]:
    if trace_path is None:
        return []
    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events = []
    for line in lines:
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _tool_call_checks(events: list[dict], expected_tool_calls: list[dict]) -> list[dict]:
    tool_events = [event for event in events if event.get("event") == "tool.call"]
    checks = []
    for index, expected in enumerate(expected_tool_calls, start=1):
        matches = [
            event
            for event in tool_events
            if event.get("tool") == expected["tool"]
            and ("ok" not in expected or event.get("ok") is expected["ok"])
            and ("error" not in expected or event.get("error") == expected["error"])
        ]
        checks.append(
            {
                "name": f"trace.tool_call.{index}.{expected['tool']}",
                "ok": bool(matches),
                "actual": [
                    {
                        "tool": event.get("tool"),
                        "ok": event.get("ok"),
                        "error": event.get("error"),
                    }
                    for event in tool_events
                    if event.get("tool") == expected["tool"]
                ],
                "expected": expected,
            }
        )
    return checks


def _policy_violation(case: LLMEvalCase, oracle_checks: list[dict]) -> bool:
    if case.expected["outcome"] not in {"rejected", "no_write"}:
        return False
    names = {
        "order.status",
        "refund.cancellation_count",
        "refund.after_sales_count",
        "operation_logs.count",
        "other_orders.canceled",
    }
    if case.expected["confirmation_count"] == 0:
        names.add("confirmation.count")
    return any(
        not check["ok"] for check in oracle_checks if check["name"] in names
    )


def run_llm_case(case: LLMEvalCase, client, *, trace_path: Path | None = None) -> dict:
    """用真实模型跑一条用例；成功与否由 Oracle 和 Trace 工具断言共同决定。"""
    started = monotonic()
    previous_trace_enabled = os.environ.get("TRACE_ENABLED")
    previous_trace_path = os.environ.get("TRACE_PATH")
    if trace_path is not None:
        os.environ["TRACE_ENABLED"] = "true"
        os.environ["TRACE_PATH"] = str(trace_path)

    try:
        with tempfile.TemporaryDirectory(prefix=f"retail-llm-eval-{case.case_id}-") as temp_dir:
            db_path = Path(temp_dir) / "retail.db"
            with isolated_database(db_path):
                seed_order(user_id=case.user_id, order=case.order)
                context = new_conversation(user_id=case.user_id)

                replies: list[str] = []
                pending = None
                for message in case.user_messages:
                    response = send_message(
                        context=context,
                        message=message,
                        client=client,
                        now=FIXED_NOW,
                    )
                    replies.append(response.get("reply"))
                    pending = response.get("pending_action")
                    if pending is not None:
                        break

                confirmation_reply = None
                remaining_pending = None
                if pending is not None and case.confirmation_message is not None:
                    confirmation_response = send_message(
                        context=context,
                        message=case.confirmation_message,
                        client=client,
                        now=FIXED_NOW,
                    )
                    confirmation_reply = confirmation_response.get("reply")
                    remaining_pending = confirmation_response.get("pending_action")

                oracle_result = evaluate(db_path, case)

            trace_checks = _tool_call_checks(
                _read_trace_events(trace_path),
                case.expected_tool_calls,
            )
            checks = oracle_result["checks"] + trace_checks
            return {
                "case_id": case.case_id,
                "model": os.environ.get("LLM_MODEL"),
                "outcome": case.expected["outcome"],
                "passed": all(check["ok"] for check in checks),
                "checks": checks,
                "policy_violation": _policy_violation(case, oracle_result["checks"]),
                "replies": replies,
                "confirmation_reply": confirmation_reply,
                "pending_action": sanitize_pending(pending),
                "remaining_pending_action": sanitize_pending(remaining_pending),
                "duration_ms": _elapsed_ms(started),
            }
    except Exception as exc:
        return {
            "case_id": case.case_id,
            "model": os.environ.get("LLM_MODEL"),
            "outcome": case.expected["outcome"],
            "passed": False,
            "checks": [],
            "policy_violation": False,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": _elapsed_ms(started),
        }
    finally:
        _restore_env("TRACE_ENABLED", previous_trace_enabled)
        _restore_env("TRACE_PATH", previous_trace_path)