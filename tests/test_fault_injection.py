"""故障注入：提交后响应丢失、恢复查询与幂等重放。"""

import json
from dataclasses import replace

import pytest

from eval.case_schema import DEFAULT_CASES_PATH, load_case, load_cases
from eval.fault_injection import FaultConfig, FaultInjector, ResponseLost, parse_fault
from eval.harness import run_case


def read_events(path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def raw_case(case_id: str = "fault_cancel_response_lost") -> dict:
    """从真实用例文件取一条原始数据，避免在测试里重复维护整份期望。"""
    for line in DEFAULT_CASES_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        if data.get("case_id") == case_id:
            return data
    raise AssertionError(f"用例文件里没有 {case_id}")


def write_cases(tmp_path, *cases: dict):
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n",
        encoding="utf-8",
    )
    return path


def test_injector_drops_only_the_first_response():
    injector = FaultInjector(FaultConfig())

    with pytest.raises(ResponseLost):
        injector.after_commit(
            tool="cancel_order",
            confirmation_id="cf-1",
            call=lambda: {"ok": True},
        )

    assert injector.fired == 1
    assert injector.triggered is True
    # 第二次调用正常返回，故障不会无限重演。
    assert injector.after_commit(
        tool="cancel_order",
        confirmation_id="cf-1",
        call=lambda: {"ok": True},
    ) == {"ok": True}


def test_injector_does_not_fire_when_call_raises():
    injector = FaultInjector(FaultConfig())

    def boom():
        raise ValueError("业务拒绝")

    with pytest.raises(ValueError):
        injector.after_commit(tool="cancel_order", confirmation_id="cf-1", call=boom)

    assert injector.fired == 0


def test_unknown_fault_point_is_rejected():
    with pytest.raises(ValueError, match="fault.point"):
        parse_fault({"point": "before_commit"})

    with pytest.raises(ValueError, match="未知故障点"):
        FaultInjector(FaultConfig(point="before_commit"))


def test_fault_requires_a_confirmation_action(tmp_path):
    data = raw_case()
    data["confirmation"] = {"mode": "none"}

    with pytest.raises(ValueError, match="确认动作"):
        load_cases(write_cases(tmp_path, data))


def test_fault_accepts_chat_confirmation(tmp_path):
    data = raw_case()
    data["confirmation"] = {
        "mode": "chat",
        "message": "确认取消",
        "intent": "approve",
        "evidence": "确认取消",
    }

    loaded = load_cases(write_cases(tmp_path, data))

    assert loaded[0].confirmation["mode"] == "chat"
    assert loaded[0].fault is not None


def test_fault_requires_approve_decision(tmp_path):
    data = raw_case()
    data["confirmation"] = {"mode": "explicit", "decision": "reject"}

    with pytest.raises(ValueError, match="批准路径"):
        load_cases(write_cases(tmp_path, data))


def test_fault_case_must_declare_recovery_expectation(tmp_path):
    data = raw_case()
    data["expected"].pop("recovery_state")

    with pytest.raises(ValueError, match="recovery_state"):
        load_cases(write_cases(tmp_path, data))


def test_recovery_expectation_requires_fault(tmp_path):
    data = raw_case("cancel_paid_order")
    data["expected"]["recovery_state"] = "executed"

    with pytest.raises(ValueError, match="不应声明恢复期望"):
        load_cases(write_cases(tmp_path, data))


def test_cancel_fault_case_passes_and_traces_injection(tmp_path):
    case = load_case("fault_cancel_response_lost")

    result = run_case(case, trace_path=tmp_path / "trace.jsonl")

    assert result["passed"] is True, result
    assert all(item["ok"] for item in result["checks"])
    assert result["decision_error"] == "RESPONSE_LOST"
    assert result["fault"] == {"point": "after_commit", "fired": 1}
    # 响应丢了，但业务写入已经提交。
    assert result["write_rows_after_first_commit"] == result["write_rows_after_replay"]
    assert result["write_rows_after_first_commit"]["refund_requests"] == 1
    assert result["recovery"]["state"] == "executed"
    assert result["recovery"]["result"]["status"] == "canceled"

    events = read_events(tmp_path / "trace.jsonl")
    injected = [event for event in events if event["event"] == "fault.injected"]
    assert len(injected) == 1
    assert injected[0]["tool"] == "cancel_order"
    assert injected[0]["point"] == "after_commit"


def test_chat_fault_case_passes_and_traces_injection(tmp_path):
    case = load_case("fault_chat_cancel_response_lost")

    result = run_case(case, trace_path=tmp_path / "trace.jsonl")

    assert result["passed"] is True, result
    assert all(item["ok"] for item in result["checks"])
    assert result["confirmation_mode"] == "chat"
    assert result["confirmation_error"] == "RESPONSE_LOST"
    assert result["fault"] == {"point": "after_commit", "fired": 1}
    assert result["write_rows_after_first_commit"] == result["write_rows_after_replay"]
    assert result["write_rows_after_first_commit"]["refund_requests"] == 1
    assert result["recovery"]["state"] == "executed"

    events = read_events(tmp_path / "trace.jsonl")
    intents = [event for event in events if event["event"] == "confirmation.intent"]
    injected = [event for event in events if event["event"] == "fault.injected"]
    assert intents[-1]["intent"] == "approve"
    assert len(injected) == 1
    assert injected[0]["point"] == "after_commit"


def test_missing_recovery_actually_fails_the_case(tmp_path):
    """变异测试：把恢复状态改成 idle，判定必须失败，而不是跟着用例改口。"""
    case = load_case("fault_cancel_response_lost")
    mutated = replace(
        case,
        expected={**case.expected, "recovery_state": "idle"},
    )

    result = run_case(mutated, trace_path=tmp_path / "trace.jsonl")

    assert result["passed"] is False
    failed = [item["name"] for item in result["checks"] if not item["ok"]]
    assert failed == ["recovery.state"]


def test_duplicate_refund_expectation_fails_the_case(tmp_path):
    """变异测试：期望两笔退款时 Oracle 必须失败，证明它读的是数据库。"""
    case = load_case("fault_refund_response_lost")
    mutated = replace(
        case,
        expected={**case.expected, "refund_count": 2},
    )

    result = run_case(mutated, trace_path=tmp_path / "trace.jsonl")

    assert result["passed"] is False
    assert result["write_rows_after_first_commit"]["refund_requests"] == 1


def test_case_without_fault_does_not_report_recovery(tmp_path):
    case = load_case("cancel_paid_order")

    result = run_case(case, trace_path=tmp_path / "trace.jsonl")

    assert result["passed"] is True
    assert "recovery" not in result
    assert "fault" not in result
    assert all(item["name"] != "recovery.state" for item in result["checks"])
