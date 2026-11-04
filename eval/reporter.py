"""评测结果汇总：生成 summary.json。"""

import json
from pathlib import Path


def build_summary(results: list[dict], *, duration_ms: float) -> dict:
    """把逐条用例结果汇总为批量运行摘要。"""
    cases = []
    for result in results:
        checks = result.get("checks", [])
        cases.append(
            {
                "case_id": result["case_id"],
                "passed": bool(result["passed"]),
                "outcome": result.get("outcome"),
                "confirmation_mode": result.get("confirmation_mode"),
                "duration_ms": result.get("duration_ms", 0.0),
                "failed_checks": [
                    check["name"] for check in checks if not check.get("ok")
                ],
                "error": result.get("error"),
            }
        )

    passed = sum(1 for case in cases if case["passed"])
    return {
        "total": len(cases),
        "passed": passed,
        "failed": len(cases) - passed,
        "duration_ms": round(duration_ms, 2),
        "cases": cases,
    }


def write_summary(summary: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )