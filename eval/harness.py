"""最小评测执行器：脚本化模型 → 确认 → Oracle。"""

import json
import os
import tempfile
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

from openai.types.chat import ChatCompletionMessage

from app.harness.conversation import decide_confirmation, new_conversation, send_message
from eval.case_schema import EvalCase
from eval.fixtures import FIXED_NOW, isolated_database, seed_case_order
from eval.oracle import evaluate


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


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def _elapsed_ms(started: float) -> float:
    return round((monotonic() - started) * 1000, 2)


def run_case(case: EvalCase, *, trace_path: Path | None = None) -> dict:
    """在独立数据库中执行一轮取消，并用 Oracle 判定最终状态。"""
    started = monotonic()
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
                context = new_conversation(user_id=case.user_id)
                client = ScriptedClient([_tool_call_message(case)])
                response = send_message(
                    context=context,
                    message=case.user_message,
                    client=client,
                    now=FIXED_NOW,
                )

                pending = response.get("pending_action")
                if pending is None:
                    return {
                        "case_id": case.case_id,
                        "passed": False,
                        "checks": [],
                        "error": "未生成待确认操作",
                        "reply": response.get("reply"),
                        "duration_ms": _elapsed_ms(started),
                    }

                decision = decide_confirmation(
                    context=context,
                    confirmation_id=pending["confirmation_id"],
                    decision="approve",
                    now=FIXED_NOW,
                )
                oracle_result = evaluate(db_path, case)

                return {
                    "case_id": case.case_id,
                    "passed": oracle_result["passed"],
                    "checks": oracle_result["checks"],
                    "pending_action": {
                        key: pending.get(key)
                        for key in ("tool", "order_id", "summary", "status", "expires_at")
                    },
                    "decision_reply": decision.get("reply"),
                    "decision_error": decision.get("error"),
                    "duration_ms": _elapsed_ms(started),
                }
    except Exception as exc:
        return {
            "case_id": case.case_id,
            "passed": False,
            "checks": [],
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": _elapsed_ms(started),
        }
    finally:
        _restore_env("LLM_MODEL", previous_model)
        _restore_env("TRACE_ENABLED", previous_trace_enabled)
        _restore_env("TRACE_PATH", previous_trace_path)