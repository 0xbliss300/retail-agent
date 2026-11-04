"""真实 LLM 重复运行与多用例的指标汇总。"""

import math


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    rank = (len(ordered) - 1) * percentile / 100
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(ordered[lower], 2)
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)
    return round(value, 2)


def _model_name(results: list[dict], model: str | None) -> str | None:
    if model:
        return model
    return next(
        (result.get("model") for result in results if result.get("model")),
        None,
    )


def build_run_summary(results: list[dict], *, model: str | None = None) -> dict:
    """汇总同一用例的多次运行结果。"""
    runs = len(results)
    passed = sum(1 for result in results if result.get("passed"))
    durations = [float(result.get("duration_ms", 0.0)) for result in results]
    policy_violations = sum(
        1 for result in results if result.get("policy_violation")
    )

    return {
        "case_id": results[0].get("case_id") if results else None,
        "model": _model_name(results, model),
        "runs": runs,
        "passed": passed,
        "failed": runs - passed,
        "success_rate": round(passed / runs, 4) if runs else 0.0,
        "policy_violations": policy_violations,
        "avg_duration_ms": round(sum(durations) / runs, 2) if runs else 0.0,
        "p50_latency_ms": _percentile(durations, 50),
        "p95_latency_ms": _percentile(durations, 95),
        "runs_detail": [
            {
                "run": index,
                "passed": bool(result.get("passed")),
                "duration_ms": result.get("duration_ms", 0.0),
                "policy_violation": bool(result.get("policy_violation")),
                "error": result.get("error"),
                "failed_checks": [
                    check["name"]
                    for check in result.get("checks", [])
                    if not check.get("ok")
                ],
            }
            for index, result in enumerate(results, start=1)
        ],
    }


def build_multi_case_summary(
    case_summaries: list[dict],
    *,
    model: str | None = None,
    runs_per_case: int,
) -> dict:
    """汇总多条用例、每用例多次运行的指标。"""
    cases = len(case_summaries)
    total_runs = sum(summary["runs"] for summary in case_summaries)
    total_passed = sum(summary["passed"] for summary in case_summaries)
    total_violations = sum(
        summary.get("policy_violations", 0) for summary in case_summaries
    )
    durations = [
        detail["duration_ms"]
        for summary in case_summaries
        for detail in summary["runs_detail"]
    ]
    cases_with_pass = sum(1 for summary in case_summaries if summary["passed"] > 0)
    accuracy = round(total_passed / total_runs, 4) if total_runs else 0.0

    return {
        "model": _model_name(case_summaries, model),
        "cases": cases,
        "runs_per_case": runs_per_case,
        "total_runs": total_runs,
        "passed": total_passed,
        "failed": total_runs - total_passed,
        "accuracy": accuracy,
        "pass@1": accuracy,
        "pass_at_k": round(cases_with_pass / cases, 4) if cases else 0.0,
        "k": runs_per_case,
        "policy_violation_rate": (
            round(total_violations / total_runs, 4) if total_runs else 0.0
        ),
        "avg_duration_ms": (
            round(sum(durations) / total_runs, 2) if total_runs else 0.0
        ),
        "p50_latency_ms": _percentile(durations, 50),
        "p95_latency_ms": _percentile(durations, 95),
        "cases_detail": case_summaries,
    }