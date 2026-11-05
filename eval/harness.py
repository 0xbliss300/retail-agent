"""最小评测执行器：脚本化模型 → 显式/自然语言确认 → Oracle。"""

import json
import os
import tempfile
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

from openai.types.chat import ChatCompletionMessage

from app.harness.conversation import decide_confirmation, new_conversation, send_message
from app.harness.recovery import describe_recovery
from eval.case_schema import EvalCase
from eval.fault_injection import FaultInjector, ResponseLost
from eval.fixtures import FIXED_NOW, isolated_database, seed_case_order
from eval.oracle import (
    business_result_ok,
    check,
    count_write_rows,
    detect_unconfirmed_write,
    evaluate,
)


class ScriptedClient:
    """只实现 request_model 用到的最小 OpenAI 客户端接口。"""

    def __init__(self, messages: list[ChatCompletionMessage]):
        self._messages = list(messages)
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        if not self._messages:
            raise RuntimeError("脚本化模型没有更多响应")
        message = self._messages.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _tool_call_message(case: EvalCase) -> ChatCompletionMessage:
    return ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[
            {
                "id": f"call_{case.case_id}",
                "type": "function",
                "function": {
                    "name": case.tool_call["name"],
                    "arguments": json.dumps(case.tool_call["arguments"], ensure_ascii=False),
                },
            }
        ],
    )


def _text_message(content: str) -> ChatCompletionMessage:
    return ChatCompletionMessage(role="assistant", content=content)


def _intent_message(intent: str, evidence: str) -> ChatCompletionMessage:
    return ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[
            {
                "id": "call_intent",
                "type": "function",
                "function": {
                    "name": "report_confirmation_intent",
                    "arguments": json.dumps(
                        {"intent": intent, "evidence": evidence},
                        ensure_ascii=False,
                    ),
                },
            }
        ],
    )


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def _elapsed_ms(started: float) -> float:
    return round((monotonic() - started) * 1000, 2)


def sanitize_pending(pending: dict | None) -> dict | None:
    if not isinstance(pending, dict):
        return None
    return {
        key: pending.get(key)
        for key in ("tool", "order_id", "summary", "status", "expires_at")
    }


def _verdict(db_path: Path, case: EvalCase) -> dict:
    """两条跨档对比用的客观判定。"""
    return {
        "policy_violation": detect_unconfirmed_write(db_path, case),
        "business_ok": business_result_ok(db_path, case),
    }


def _fault_checks(
    *,
    case: EvalCase,
    injector: FaultInjector,
    recovery: dict,
    replay: dict,
    committed: dict,
    after: dict,
) -> list[dict]:
    """故障用例的公共判定：注入确实发生、恢复答对、重放不写。"""
    return [
        check("fault.triggered", injector.triggered, True),
        check("fault.fired_times", injector.fired, 1),
        check("recovery.state", recovery.get("state"), case.expected["recovery_state"]),
        check(
            "recovery.result_status",
            (recovery.get("result") or {}).get("status"),
            case.expected["recovery_result_status"],
        ),
        check("replay.error", replay.get("error"), None),
        check("replay.no_new_write", after == committed, True),
    ]


def _run_explicit_decision(
    *,
    case: EvalCase,
    context: dict,
    pending: dict,
    db_path: Path,
    response: dict,
    started: float,
) -> dict:
    """显式确认路径；配置了故障时额外验证恢复查询与重放。"""
    confirmation_id = pending["confirmation_id"]

    def decide() -> dict:
        return decide_confirmation(
            context=context,
            confirmation_id=confirmation_id,
            decision=case.confirmation["decision"],
            now=FIXED_NOW,
        )

    base = {
        "case_id": case.case_id,
        "outcome": case.expected["outcome"],
        "confirmation_mode": "explicit",
    }

    if case.fault is None:
        decision = decide()
        oracle_result = evaluate(db_path, case)
        return {
            **base,
            "passed": oracle_result["passed"],
            "checks": oracle_result["checks"],
            **_verdict(db_path, case),
            "reply": response.get("reply"),
            "pending_action": sanitize_pending(pending),
            "decision_reply": decision.get("reply"),
            "decision_error": decision.get("error"),
            "duration_ms": _elapsed_ms(started),
        }

    injector = FaultInjector(case.fault)
    decision = None
    try:
        decision = injector.after_commit(
            tool=pending["tool"],
            confirmation_id=confirmation_id,
            call=decide,
        )
    except ResponseLost:
        # 业务写入已经提交，丢的只是响应；恢复过程不重新执行业务逻辑。
        decision = None

    # 提交之后、重放之前的快照：重放不得新增任何业务行。
    committed = count_write_rows(db_path, case)
    # 用户重连后先问「上次到底成了没有」，随后可能重复点一次确认。
    recovery = describe_recovery(
        user_id=case.user_id,
        session_id=context["session_id"],
        thread_id=context["thread_id"],
        now=FIXED_NOW,
    )
    replay = decide()
    after = count_write_rows(db_path, case)

    extra_checks = _fault_checks(
        case=case,
        injector=injector,
        recovery=recovery,
        replay=replay,
        committed=committed,
        after=after,
    )
    oracle_result = evaluate(db_path, case)
    return {
        **base,
        "passed": oracle_result["passed"] and all(item["ok"] for item in extra_checks),
        "checks": oracle_result["checks"] + extra_checks,
        **_verdict(db_path, case),
        "reply": response.get("reply"),
        "pending_action": sanitize_pending(pending),
        "decision_reply": decision.get("reply") if decision is not None else None,
        "decision_error": "RESPONSE_LOST" if decision is None else decision.get("error"),
        "fault": {
            "point": case.fault.point,
            "fired": injector.fired,
        },
        "recovery": recovery,
        "replay_reply": replay.get("reply"),
        "write_rows_after_first_commit": committed,
        "write_rows_after_replay": after,
        "duration_ms": _elapsed_ms(started),
    }


def _run_chat_decision(
    *,
    case: EvalCase,
    context: dict,
    pending: dict,
    db_path: Path,
    client,
    response: dict,
    started: float,
) -> dict:
    """自然语言确认路径；故障注入点包住整轮 send_message。"""
    confirmation_id = pending["confirmation_id"]

    def send_confirmation() -> dict:
        return send_message(
            context=context,
            message=case.confirmation["message"],
            client=client,
            now=FIXED_NOW,
        )

    base = {
        "case_id": case.case_id,
        "outcome": case.expected["outcome"],
        "confirmation_mode": "chat",
    }

    if case.fault is None:
        confirmation_response = send_confirmation()
        oracle_result = evaluate(db_path, case)
        return {
            **base,
            "passed": oracle_result["passed"],
            "checks": oracle_result["checks"],
            **_verdict(db_path, case),
            "reply": response.get("reply"),
            "confirmation_reply": confirmation_response.get("reply"),
            "pending_action": sanitize_pending(pending),
            "remaining_pending_action": sanitize_pending(
                confirmation_response.get("pending_action")
            ),
            "duration_ms": _elapsed_ms(started),
        }

    injector = FaultInjector(case.fault)
    confirmation_response = None
    try:
        confirmation_response = injector.after_commit(
            tool=pending["tool"],
            confirmation_id=confirmation_id,
            call=send_confirmation,
        )
    except ResponseLost:
        # 确认已经提交，丢的是整轮回复；恢复过程不重新执行业务逻辑。
        confirmation_response = None

    committed = count_write_rows(db_path, case)
    recovery = describe_recovery(
        user_id=case.user_id,
        session_id=context["session_id"],
        thread_id=context["thread_id"],
        now=FIXED_NOW,
    )
    # 恢复后已无待确认记录，再发一条消息只会得到普通回复；
    # 重放改走显式确认接口，验证同一确认编号不会被二次执行。
    replay = decide_confirmation(
        context=context,
        confirmation_id=confirmation_id,
        decision="approve",
        now=FIXED_NOW,
    )
    after = count_write_rows(db_path, case)

    extra_checks = _fault_checks(
        case=case,
        injector=injector,
        recovery=recovery,
        replay=replay,
        committed=committed,
        after=after,
    )
    oracle_result = evaluate(db_path, case)
    return {
        **base,
        "passed": oracle_result["passed"] and all(item["ok"] for item in extra_checks),
        "checks": oracle_result["checks"] + extra_checks,
        **_verdict(db_path, case),
        "reply": response.get("reply"),
        "confirmation_reply": (
            confirmation_response.get("reply") if confirmation_response is not None else None
        ),
        "confirmation_error": "RESPONSE_LOST" if confirmation_response is None else None,
        "pending_action": sanitize_pending(pending),
        "remaining_pending_action": None,
        "fault": {
            "point": case.fault.point,
            "fired": injector.fired,
        },
        "recovery": recovery,
        "replay_reply": replay.get("reply"),
        "write_rows_after_first_commit": committed,
        "write_rows_after_replay": after,
        "duration_ms": _elapsed_ms(started),
    }


def run_case(case: EvalCase, *, trace_path: Path | None = None) -> dict:
    """在独立数据库中执行一条用例，并用 Oracle 判定最终状态。"""
    started = monotonic()
    mode = case.confirmation["mode"]
    previous_model = os.environ.get("LLM_MODEL")
    previous_trace_enabled = os.environ.get("TRACE_ENABLED")
    previous_trace_path = os.environ.get("TRACE_PATH")
    os.environ.setdefault("LLM_MODEL", "scripted-eval")
    if trace_path is not None:
        os.environ["TRACE_ENABLED"] = "true"
        os.environ["TRACE_PATH"] = str(trace_path)

    try:
        with tempfile.TemporaryDirectory(prefix=f"retail-eval-{case.case_id}-") as temp_dir:
            db_path = Path(temp_dir) / "retail.db"
            with isolated_database(db_path):
                seed_case_order(case)

                responses = [_tool_call_message(case)]
                if mode == "none":
                    responses.append(_text_message("该操作当前无法执行，本次未修改任何数据。"))
                elif mode == "chat":
                    responses.append(
                        _intent_message(
                            case.confirmation["intent"],
                            case.confirmation["evidence"],
                        )
                    )
                client = ScriptedClient(responses)

                context = new_conversation(user_id=case.user_id)
                response = send_message(
                    context=context,
                    message=case.user_message,
                    client=client,
                    now=FIXED_NOW,
                )
                pending = response.get("pending_action")

                if mode == "none":
                    oracle_result = evaluate(db_path, case)
                    return {
                        "case_id": case.case_id,
                        "outcome": case.expected["outcome"],
                        "confirmation_mode": "none",
                        "passed": oracle_result["passed"],
                        "checks": oracle_result["checks"],
                        **_verdict(db_path, case),
                        "reply": response.get("reply"),
                        "pending_action": sanitize_pending(pending),
                        "duration_ms": _elapsed_ms(started),
                    }

                if pending is None:
                    return {
                        "case_id": case.case_id,
                        "outcome": case.expected["outcome"],
                        "confirmation_mode": mode,
                        "passed": False,
                        "checks": [],
                        **_verdict(db_path, case),
                        "error": "未生成待确认操作",
                        "reply": response.get("reply"),
                        "duration_ms": _elapsed_ms(started),
                    }

                if mode == "explicit":
                    return _run_explicit_decision(
                        case=case,
                        context=context,
                        pending=pending,
                        db_path=db_path,
                        response=response,
                        started=started,
                    )

                return _run_chat_decision(
                    case=case,
                    context=context,
                    pending=pending,
                    db_path=db_path,
                    client=client,
                    response=response,
                    started=started,
                )
    except Exception as exc:
        return {
            "case_id": case.case_id,
            "outcome": case.expected["outcome"],
            "confirmation_mode": mode,
            "passed": False,
            "checks": [],
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": _elapsed_ms(started),
        }
    finally:
        _restore_env("LLM_MODEL", previous_model)
        _restore_env("TRACE_ENABLED", previous_trace_enabled)
        _restore_env("TRACE_PATH", previous_trace_path)