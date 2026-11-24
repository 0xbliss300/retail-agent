"""真实 LLM 评测：模型自己选工具，Oracle + Trace 断言。"""

import json
import os
import re
import tempfile
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from time import monotonic

from app.harness.conversation import new_conversation, send_message
from eval.fixtures import FIXED_NOW, isolated_database, seed_order
from eval.harness import sanitize_pending
from eval.llm_case_schema import LLMEvalCase
from eval.oracle import check, check_refunds_before_approval, evaluate

_YUAN_NUMBER = r"-?\d+(?:\.\d{1,2})?"
_YUAN_MONEY = re.compile(
    rf"[¥￥]\s*({_YUAN_NUMBER})(?!\d|\.\d)|(?<![\d.,-])({_YUAN_NUMBER})\s*(?:元|块)"
)


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


def _tool_call_checks(
    tool_events: list[dict], expected_tool_calls: list[dict], *, exact: bool = False,
) -> list[dict]:
    remaining = iter(tool_events)
    checks = []
    if exact:
        checks.append({
            "name": "trace.tool_call_count",
            "ok": len(tool_events) == len(expected_tool_calls),
            "actual": len(tool_events),
            "expected": len(expected_tool_calls),
        })
    for index, expected in enumerate(expected_tool_calls, start=1):
        candidates = tool_events[index - 1 : index] if exact else remaining
        matched = any(all(event.get(key) == value for key, value in expected.items()) for event in candidates)
        checks.append(
            {
                "name": f"trace.tool_call.{index}.{expected['tool']}",
                "ok": matched,
                "actual": [
                    {key: event.get(key) for key in expected}
                    for event in (tool_events[index - 1 : index] if exact else tool_events)
                    if exact or event.get("tool") == expected["tool"]
                ],
                "expected": expected,
            }
        )
    return checks


def _trace_observations(events: list[dict]) -> dict:
    turn_by_run = {
        event["run_id"]: turn
        for turn, event in enumerate(
            (event for event in events if event.get("event") == "agent.start"), start=1
        )
    }
    tool_calls = [
        {**event, "turn": turn_by_run.get(event["run_id"])}
        for event in events if event.get("event") == "tool.call"
    ]
    return {
        "tool_calls": tool_calls,
        "logistics_order_ids": [
            call["order_id"] for call in tool_calls
            if call["tool"] == "get_logistics" and call["ok"] and "order_id" in call
        ],
        "turn_duration_ms": [
            event["duration_ms"] for event in events if event.get("event") == "agent.end"
        ],
    }


def _model_tool_requests(messages: list[dict]) -> list[dict]:
    # 原样保留请求参数，便于定位被运行时拦下的调用和错误订单号。
    requests = []
    turn = 0
    for message in messages:
        if message["role"] == "user":
            turn += 1
        for call in message.get("tool_calls", []):
            requests.append({"turn": turn, **call["function"]})
    return requests


def _reply_checks(replies: list[str], expected: list[list[str]]) -> list[dict]:
    checks = []
    for turn, terms in enumerate(expected, start=1):
        reply = (replies[turn - 1] or "") if len(replies) >= turn else ""
        checks.append(check(f"reply.turn.{turn}.contains", [term for term in terms if term in reply], terms))
    return checks


def _reply_amount_checks(replies: list[str], expected_amounts: list[int]) -> list[dict]:
    checks = []
    for index, amount in enumerate(expected_amounts):
        reply = replies[index] if index < len(replies) else ""
        actual = sorted({
            int(Decimal(prefixed or suffixed) * 100)
            for prefixed, suffixed in _YUAN_MONEY.findall(reply.replace("*", ""))
        })
        checks.append(check(f"followup.turn.{index + 1}.reply_amount_cents", actual, [amount]))
    return checks


def _policy_violation(case: LLMEvalCase, oracle_checks: list[dict]) -> bool:
    if any(item["name"] == "before_approval.refund_count" and not item["ok"] for item in oracle_checks):
        return True
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
    """用真实模型跑一条用例，核对数据库、工具及指定的回复证据。"""
    if trace_path is None:
        with tempfile.TemporaryDirectory(prefix="retail-llm-trace-") as temp_dir:
            return run_llm_case(case, client, trace_path=Path(temp_dir) / "trace.jsonl")

    started = monotonic()
    context = new_conversation(user_id=case.user_id)
    metadata = {
        "case_id": case.case_id,
        "model": os.environ.get("LLM_MODEL"),
        "prompt_sha256": sha256(context["messages"][0]["content"].encode("utf-8")).hexdigest(),
        "outcome": case.expected["outcome"],
        "user_messages": case.user_messages,
        "followup_messages": case.followup_messages,
    }
    previous_trace_enabled = os.environ.get("TRACE_ENABLED")
    previous_trace_path = os.environ.get("TRACE_PATH")

    replies: list[str] = []
    followup_replies: list[str] = []
    try:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text("", encoding="utf-8")
        os.environ["TRACE_ENABLED"] = "true"
        os.environ["TRACE_PATH"] = str(trace_path)
        with tempfile.TemporaryDirectory(prefix=f"retail-llm-eval-{case.case_id}-") as temp_dir:
            db_path = Path(temp_dir) / "retail.db"
            with isolated_database(db_path):
                for order in [case.order, *case.additional_orders]:
                    seed_order(user_id=case.user_id, order=order)
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
                followup_checks = []
                if case.followup_messages:
                    order_id = pending["order_id"] if pending else None
                    pending_order = check("followup.pending_order", order_id, case.order["order_id"])
                    followup_checks.append(pending_order)
                    if pending_order["ok"] and pending is not None:
                        for turn, message in enumerate(case.followup_messages, start=1):
                            response = send_message(context=context, message=message, client=client, now=FIXED_NOW)
                            followup_replies.append(response["reply"])
                            after = response["pending_action"]
                            same = bool(
                                after and after["status"] == "pending"
                                and after["confirmation_id"] == pending["confirmation_id"]
                            )
                            followup_checks.append(check(f"followup.turn.{turn}.same_confirmation", same, True))
                            if not same:
                                break
                    followup_checks.append(check_refunds_before_approval(db_path, case.user_id))

                if (
                    pending is not None and case.confirmation_message is not None
                    and all(item["ok"] for item in followup_checks)
                ):
                    confirmation_response = send_message(
                        context=context,
                        message=case.confirmation_message,
                        client=client,
                        now=FIXED_NOW,
                    )
                    confirmation_reply = confirmation_response.get("reply")
                    remaining_pending = confirmation_response.get("pending_action")

                oracle_result = evaluate(db_path, case, unchanged_orders=case.additional_orders)

            observations = _trace_observations(_read_trace_events(trace_path))
            trace_checks = _tool_call_checks(
                observations["tool_calls"],
                case.expected_tool_calls,
                exact=case.expected.get("exact_tool_calls", False),
            )
            checks = (
                oracle_result["checks"] + followup_checks + trace_checks
                + _reply_checks(replies, case.expected.get("reply_contains", []))
                + _reply_amount_checks(followup_replies, case.expected.get("followup_reply_amount_cents", []))
            )
            return {
                **metadata,
                **observations,
                "model_tool_requests": _model_tool_requests(context["messages"]),
                "passed": all(check["ok"] for check in checks),
                "checks": checks,
                "policy_violation": _policy_violation(case, oracle_result["checks"] + followup_checks),
                "replies": replies,
                "followup_replies": followup_replies,
                "confirmation_reply": confirmation_reply,
                "pending_action": sanitize_pending(pending),
                "remaining_pending_action": sanitize_pending(remaining_pending),
                "duration_ms": _elapsed_ms(started),
            }
    except Exception as exc:
        return {
            **metadata,
            **_trace_observations(_read_trace_events(trace_path)),
            "model_tool_requests": _model_tool_requests(context["messages"]),
            "replies": replies,
            "followup_replies": followup_replies,
            "passed": False,
            "checks": [],
            "policy_violation": False,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": _elapsed_ms(started),
        }
    finally:
        _restore_env("TRACE_ENABLED", previous_trace_enabled)
        _restore_env("TRACE_PATH", previous_trace_path)
