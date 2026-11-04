"""真实 LLM 冒烟评测的离线管道测试，使用假客户端避免真实网络。"""

import json
import sys

from openai.types.chat import ChatCompletionMessage

from eval import run_eval
from eval.harness import ScriptedClient
from eval.llm_case_schema import load_llm_case
from eval.llm_harness import run_llm_case


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