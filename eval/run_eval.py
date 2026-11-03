"""最小评测 CLI：运行一条用例并写出结果。"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from eval.case_schema import load_case
from eval.harness import run_case

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_ROOT = PROJECT_ROOT / "reports" / "runs"


def main() -> int:
    parser = argparse.ArgumentParser(description="运行最小评测用例")
    parser.add_argument("--case", default="cancel_paid_order", help="用例编号")
    parser.add_argument("--output", type=Path, default=None, help="结果目录")
    args = parser.parse_args()

    try:
        case = load_case(args.case)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2

    output_dir = args.output or REPORTS_ROOT / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir.mkdir(parents=True, exist_ok=True)
    result = run_case(case, trace_path=output_dir / "trace.jsonl")
    result_path = output_dir / "result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"{'PASS' if result['passed'] else 'FAIL'} {case.case_id}")
    for check in result.get("checks", []):
        marker = "OK" if check["ok"] else "FAIL"
        print(
            f"  [{marker}] {check['name']}: "
            f"{check['actual']!r} (expected {check['expected']!r})"
        )
    if result.get("error"):
        print(f"  error: {result['error']}")
    print(f"result: {result_path}")
    print(f"trace:  {output_dir / 'trace.jsonl'}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())