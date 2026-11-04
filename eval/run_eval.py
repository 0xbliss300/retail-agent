"""最小评测 CLI：单条运行或批量运行并写出结果。"""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from eval.baseline_harness import load_baseline_cases, run_baseline_case
from eval.case_schema import load_case, load_cases
from eval.harness import run_case
from eval.llm_case_schema import load_llm_case, load_llm_cases
from eval.llm_harness import run_llm_case
from eval.metrics import build_multi_case_summary, build_run_summary
from eval.reporter import (
    build_comparison,
    build_summary,
    render_comparison_markdown,
    write_summary,
)

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


def _create_llm_client():
    try:
        from app.agent.model_client import create_client

        client = create_client()
    except Exception as exc:
        raise RuntimeError(f"无法创建模型客户端: {exc}") from exc
    if not os.environ.get("LLM_MODEL"):
        raise RuntimeError("缺少 LLM_MODEL 配置")
    return client


def _run_llm_once(case, run_dir: Path) -> dict:
    client = _create_llm_client()
    result = run_llm_case(case, client, trace_path=run_dir / "trace.jsonl")
    _write_result(result, run_dir)
    return result


def _print_failed_checks(result: dict) -> None:
    for check in result.get("checks", []):
        if not check["ok"]:
            print(
                f"  [{check['name']}] {check['actual']!r} "
                f"(expected {check['expected']!r})"
            )
    if result.get("error"):
        print(f"  error: {result['error']}")


def _execute_llm_runs(
    case,
    output_dir: Path,
    runs: int,
    *,
    label: str | None = None,
) -> tuple[list[dict] | None, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for index in range(1, runs + 1):
        run_dir = output_dir if runs == 1 else output_dir / f"run-{index:02d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = _run_llm_once(case, run_dir)
        except RuntimeError as exc:
            prefix = f"{label} run {index}/{runs}" if label else f"run {index}/{runs}"
            print(f"ERROR: {prefix}: {exc}")
            return None, 2
        results.append(result)

        if runs > 1:
            prefix = f"{label} run {index}/{runs}" if label else f"run {index}/{runs}"
            print(f"{prefix}: {'PASS' if result['passed'] else 'FAIL'}")
            if not result["passed"]:
                _print_failed_checks(result)
        elif label is not None:
            print(f"{label}: {'PASS' if result['passed'] else 'FAIL'}")
            if not result["passed"]:
                _print_failed_checks(result)
    return results, 0


def _run_llm(case_id: str, *, output_dir: Path, runs: int = 1) -> int:
    try:
        case = load_llm_case(case_id)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2

    results, code = _execute_llm_runs(case, output_dir, runs)
    if results is None:
        return code

    if runs == 1:
        result = results[0]
        _print_result(result, detailed=True)
        print(f"result: {output_dir / 'result.json'}")
        print(f"trace:  {output_dir / 'trace.jsonl'}")
        return 0 if result["passed"] else 1

    summary = build_run_summary(results, model=os.environ.get("LLM_MODEL"))
    summary_path = output_dir / "summary.json"
    write_summary(summary, summary_path)
    print(
        f"summary: {summary['passed']}/{summary['runs']} passed "
        f"({summary['success_rate'] * 100:.1f}%)"
    )
    print(f"avg_duration_ms: {summary['avg_duration_ms']}")
    print(f"p50_latency_ms: {summary['p50_latency_ms']}")
    print(f"p95_latency_ms: {summary['p95_latency_ms']}")
    print(f"policy_violations: {summary['policy_violations']}")
    print(f"result: {summary_path}")
    return 0 if summary["failed"] == 0 else 1


def _run_llm_all(*, output_dir: Path, runs: int = 1) -> int:
    try:
        cases = load_llm_cases()
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2

    case_summaries = []
    for case in cases:
        case_dir = output_dir / case.case_id
        results, code = _execute_llm_runs(
            case,
            case_dir,
            runs,
            label=case.case_id,
        )
        if results is None:
            return code
        case_summary = build_run_summary(results, model=os.environ.get("LLM_MODEL"))
        write_summary(case_summary, case_dir / "summary.json")
        case_summaries.append(case_summary)
        if runs > 1:
            print(
                f"{case.case_id}: "
                f"{case_summary['passed']}/{case_summary['runs']} passed"
            )

    summary = build_multi_case_summary(
        case_summaries,
        model=os.environ.get("LLM_MODEL"),
        runs_per_case=runs,
    )
    summary_path = output_dir / "summary.json"
    write_summary(summary, summary_path)
    print(
        f"accuracy: {summary['accuracy'] * 100:.1f}% "
        f"({summary['passed']}/{summary['total_runs']} runs)"
    )
    print(f"pass@1: {summary['pass@1'] * 100:.1f}%")
    if runs > 1:
        print(f"pass@{runs}: {summary['pass_at_k'] * 100:.1f}%")
    print(f"policy_violation_rate: {summary['policy_violation_rate'] * 100:.1f}%")
    print(f"avg_duration_ms: {summary['avg_duration_ms']}")
    print(f"p50_latency_ms: {summary['p50_latency_ms']}")
    print(f"p95_latency_ms: {summary['p95_latency_ms']}")
    print(f"result: {summary_path}")
    return 0 if summary["failed"] == 0 else 1


def _run_baseline_b(*, output_dir: Path) -> int:
    """只运行 B 档（无确认）用例。"""
    try:
        cases = load_baseline_cases()
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2

    results = []
    started = monotonic()
    for case in cases:
        case_dir = output_dir / case["case_id"]
        case_dir.mkdir(parents=True, exist_ok=True)
        result = run_baseline_case(case)
        _write_result(result, case_dir)
        results.append(result)
        _print_result(result, detailed=False)

    summary = build_summary(results, duration_ms=(monotonic() - started) * 1000)
    summary_path = output_dir / "summary.json"
    write_summary(summary, summary_path)
    print(f"summary: {summary['passed']}/{summary['total']} passed (baseline B)")
    print(f"result: {summary_path}")
    return 0 if summary["failed"] == 0 else 1


def _run_compare(*, output_dir: Path) -> int:
    """同一批取消场景分别跑 B、C 两档，输出可比较的指标与报告。"""
    try:
        b_cases = load_baseline_cases()
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2

    b_results = []
    c_results = []
    for case in b_cases:
        case_id = case["case_id"]
        b_dir = output_dir / "b" / case_id
        b_dir.mkdir(parents=True, exist_ok=True)
        b_results.append(run_baseline_case(case))
        _write_result(b_results[-1], b_dir)

        try:
            c_case = load_case(case_id)
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 2
        c_dir = output_dir / "c" / case_id
        c_dir.mkdir(parents=True, exist_ok=True)
        c_results.append(run_case(c_case, trace_path=c_dir / "trace.jsonl"))
        _write_result(c_results[-1], c_dir)

    comparison = build_comparison(b_results, c_results)
    comparison_path = output_dir / "comparison.json"
    write_summary(comparison, comparison_path)

    markdown = render_comparison_markdown(comparison)
    report_path = PROJECT_ROOT / "reports" / "baseline_comparison.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(markdown, encoding="utf-8")
    (output_dir / "baseline_comparison.md").write_text(markdown, encoding="utf-8")

    for key in ("b", "c"):
        item = comparison["baselines"][key]
        print(
            f"{key.upper()} · {item['name']}: "
            f"业务达成 {item['business_success_rate'] * 100:.1f}%, "
            f"未确认写入 {item['unconfirmed_write_rate'] * 100:.1f}%"
        )
    print(f"result: {comparison_path}")
    print(f"report: {report_path}")
    return 0


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
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="真实 LLM 重复运行次数，1-10",
    )
    parser.add_argument(
        "--baseline",
        choices=["c", "b"],
        default="c",
        help="脚本化用例的基线档位：c 为默认，b 跳过用户确认",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="同一批取消场景分别运行 B、C 两档并生成对照报告",
    )
    args = parser.parse_args()

    if args.llm and (args.compare or args.baseline != "c"):
        print("ERROR: --compare 与 --baseline 只用于脚本化用例")
        return 2

    if args.runs < 1 or args.runs > 10:
        print("ERROR: --runs 必须在 1 到 10 之间")
        return 2
    if not args.llm and args.runs != 1:
        print("ERROR: --runs 只用于 --llm")
        return 2

    if args.llm:
        if args.all:
            output_dir = args.output or (_default_output_dir() / "llm-all")
            return _run_llm_all(output_dir=output_dir, runs=args.runs)
        case_id = args.case or "llm_cancel_paid_order"
        output_dir = args.output or _default_llm_output_dir(case_id)
        return _run_llm(case_id, output_dir=output_dir, runs=args.runs)

    if args.compare:
        output_dir = args.output or (_default_output_dir() / "compare")
        return _run_compare(output_dir=output_dir)
    if args.baseline == "b":
        output_dir = args.output or (_default_output_dir() / "baseline-b")
        return _run_baseline_b(output_dir=output_dir)

    output_dir = args.output or _default_output_dir()
    if args.all:
        return _run_all(output_dir=output_dir)
    return _run_single(args.case or "cancel_paid_order", output_dir=output_dir)


if __name__ == "__main__":
    raise SystemExit(main())