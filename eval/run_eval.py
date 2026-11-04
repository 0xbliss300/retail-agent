"""最小评测 CLI：单条运行或批量运行并写出结果。"""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from eval.case_schema import load_case, load_cases
from eval.harness import run_case
from eval.llm_case_schema import load_llm_case
from eval.llm_harness import run_llm_case
from eval.reporter import build_summary, write_summary

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_ROOT = PROJECT_ROOT / "reports" / "runs"


def _default_output_dir() -> Path:
    return REPORTS_ROOT / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _default_llm_output_dir(case_id: str) -> Path:
    return _default_output_dir() / case_id


def _write_result(result: dict, output_dir: Path) -> Path:
    path = output_dir / "result.json"
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _print_result(result: dict, *, detailed: bool) -> None:
    print(f"{'PASS' if result['passed'] else 'FAIL'} {result['case_id']}")
    if result.get("model"):
        print(f"  model: {result['model']}")
    for check in result.get("checks", []):
        if detailed or not check["ok"]:
            marker = "OK" if check["ok"] else "FAIL"
            print(
                f"  [{marker}] {check['name']}: "
                f"{check['actual']!r} (expected {check['expected']!r})"
            )
    if result.get("error"):
        print(f"  error: {result['error']}")


def _run_all(*, output_dir: Path) -> int:
    try:
        cases = load_cases()
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2

    started = monotonic()
    results = []
    for case in cases:
        case_dir = output_dir / case.case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        result = run_case(case, trace_path=case_dir / "trace.jsonl")
        _write_result(result, case_dir)
        results.append(result)
        _print_result(result, detailed=False)

    summary = build_summary(
        results,
        duration_ms=(monotonic() - started) * 1000,
    )
    summary_path = output_dir / "summary.json"
    write_summary(summary, summary_path)
    print(f"summary: {summary['passed']}/{summary['total']} passed")
    print(f"result: {summary_path}")
    return 0 if summary["failed"] == 0 else 1


def _run_single(case_id: str, *, output_dir: Path) -> int:
    try:
        case = load_case(case_id)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    result = run_case(case, trace_path=output_dir / "trace.jsonl")
    result_path = _write_result(result, output_dir)
    _print_result(result, detailed=True)
    print(f"result: {result_path}")
    print(f"trace:  {output_dir / 'trace.jsonl'}")
    return 0 if result["passed"] else 1


def _run_llm(case_id: str, *, output_dir: Path) -> int:
    try:
        case = load_llm_case(case_id)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2

    try:
        from app.agent.model_client import create_client

        client = create_client()
    except Exception as exc:
        print(f"ERROR: 无法创建模型客户端: {exc}")
        return 2
    if not os.environ.get("LLM_MODEL"):
        print("ERROR: 缺少 LLM_MODEL 配置")
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    result = run_llm_case(case, client, trace_path=output_dir / "trace.jsonl")
    result_path = _write_result(result, output_dir)
    _print_result(result, detailed=True)
    print(f"result: {result_path}")
    print(f"trace:  {output_dir / 'trace.jsonl'}")
    return 0 if result["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="运行最小评测用例")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--case",
        default=None,
        help="用例编号；脚本化默认 cancel_paid_order，LLM 默认 llm_cancel_paid_order",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="运行 dev_cases.jsonl 中的全部脚本化用例",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="使用真实 LLM 运行 llm_cases.jsonl 中的冒烟用例",
    )
    parser.add_argument("--output", type=Path, default=None, help="结果目录")
    args = parser.parse_args()

    if args.llm:
        if args.all:
            print("ERROR: --llm 暂不支持 --all")
            return 2
        case_id = args.case or "llm_cancel_paid_order"
        output_dir = args.output or _default_llm_output_dir(case_id)
        return _run_llm(case_id, output_dir=output_dir)

    output_dir = args.output or _default_output_dir()
    if args.all:
        return _run_all(output_dir=output_dir)
    return _run_single(args.case or "cancel_paid_order", output_dir=output_dir)


if __name__ == "__main__":
    raise SystemExit(main())