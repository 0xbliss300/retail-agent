"""B 档评测执行器：直接执行写操作，用同一套 Oracle 判定。

与 C 档（eval/harness.py）共用隔离数据库、固定时钟与 Oracle，
差别只在于写操作不需要用户确认。
"""

import json
import tempfile
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

from baselines.rule_guarded import cancel_without_confirmation
from eval.fixtures import FIXED_NOW, isolated_database, seed_order
from eval.oracle import business_result_ok, detect_unconfirmed_write, evaluate

DEFAULT_BASELINE_CASES_PATH = Path(__file__).with_name("baseline_cases.jsonl")
BASELINE_TOOLS = {"cancel_order"}


def load_baseline_cases(path: Path | None = None) -> list[dict]:
    """读取 B 档用例；格式与开发集同构，但 expected 描述无确认记录的结果。"""
    cases_path = path if path is not None else DEFAULT_BASELINE_CASES_PATH
    cases: list[dict] = []
    seen: set[str] = set()
    for line_number, line in enumerate(
        cases_path.read_text(encoding="utf-8").splitlines(), start=1,
    ):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"B 档用例第 {line_number} 行不是合法 JSON: {cases_path}"
            ) from exc
        case_id = data.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"B 档用例第 {line_number} 行缺少 case_id")
        if case_id in seen:
            raise ValueError(f"B 档用例编号重复: {case_id}")
        tool_name = (data.get("tool_call") or {}).get("name")
        if tool_name not in BASELINE_TOOLS:
            raise ValueError(f"{case_id}: B 档只支持 cancel_order，收到 {tool_name!r}")
        if "expected" not in data or "order" not in data:
            raise ValueError(f"{case_id}: 缺少 order 或 expected")
        seen.add(case_id)
        cases.append(data)
    if not cases:
        raise ValueError(f"B 档用例文件没有有效用例: {cases_path}")
    return cases


def run_baseline_case(case: dict, *, trace_path: Path | None = None) -> dict:
    """在隔离数据库中直接执行写操作，再交给 Oracle 判定。"""
    started = monotonic()
    try:
        with tempfile.TemporaryDirectory(
            prefix=f"retail-eval-b-{case['case_id']}-"
        ) as temp_dir:
            db_path = Path(temp_dir) / "retail.db"
            with isolated_database(db_path):
                seed_order(user_id=case["user_id"], order=case["order"])
                tool = case["tool_call"]
                outcome = cancel_without_confirmation(
                    user_id=case["user_id"],
                    order_id=tool["arguments"]["order_id"],
                    reason=tool["arguments"]["reason"],
                    now=FIXED_NOW,
                )
                view = SimpleNamespace(
                    user_id=case["user_id"],
                    order=case["order"],
                    expected=case["expected"],
                )
                oracle_result = evaluate(db_path, view)
                return {
                    "case_id": case["case_id"],
                    "baseline": "b",
                    "outcome": case["expected"].get("outcome"),
                    "confirmation_mode": "none",
                    "passed": oracle_result["passed"],
                    "checks": oracle_result["checks"],
                    "policy_violation": detect_unconfirmed_write(db_path, view),
                    "business_ok": business_result_ok(db_path, view),
                    "tool_result": outcome,
                    "duration_ms": round((monotonic() - started) * 1000, 2),
                }
    except Exception as exc:
        return {
            "case_id": case.get("case_id"),
            "baseline": "b",
            "outcome": (case.get("expected") or {}).get("outcome"),
            "confirmation_mode": "none",
            "passed": False,
            "checks": [],
            "policy_violation": False,
            "business_ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": round((monotonic() - started) * 1000, 2),
        }
