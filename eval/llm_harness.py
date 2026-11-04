"""真实 LLM 冒烟评测：模型自己选工具，Oracle 判定数据库。"""

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


def run_llm_case(case: LLMEvalCase, client, *, trace_path: Path | None = None) -> dict:
    """用真实模型跑一条冒烟用例；成功与否只看 Oracle。"""
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
                if pending is not None:
                    confirmation_response = send_message(
                        context=context,
                        message=case.confirmation_message,
                        client=client,
                        now=FIXED_NOW,
                    )
                    confirmation_reply = confirmation_response.get("reply")
                    remaining_pending = confirmation_response.get("pending_action")

                oracle_result = evaluate(db_path, case)
                return {
                    "case_id": case.case_id,
                    "model": os.environ.get("LLM_MODEL"),
                    "outcome": case.expected["outcome"],
                    "passed": oracle_result["passed"],
                    "checks": oracle_result["checks"],
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
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": _elapsed_ms(started),
        }
    finally:
        _restore_env("TRACE_ENABLED", previous_trace_enabled)
        _restore_env("TRACE_PATH", previous_trace_path)