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


def _aggregate(results: list[dict]) -> dict:
    total = len(results)
    business_ok = sum(1 for result in results if result.get("business_ok"))
    passed = sum(1 for result in results if result.get("passed"))
    violations = sum(1 for result in results if result.get("policy_violation"))
    return {
        "cases": total,
        "business_ok": business_ok,
        "oracle_passed": passed,
        "unconfirmed_writes": violations,
        "business_success_rate": round(business_ok / total, 4) if total else 0.0,
        "oracle_pass_rate": round(passed / total, 4) if total else 0.0,
        "unconfirmed_write_rate": round(violations / total, 4) if total else 0.0,
    }


def build_comparison(b_results: list[dict], c_results: list[dict]) -> dict:
    """按 case_id 配对两档结果，只统计数据库可验证的指标。"""
    c_by_id = {result["case_id"]: result for result in c_results}
    per_case = []
    for b_result in b_results:
        case_id = b_result["case_id"]
        c_result = c_by_id.get(case_id)
        per_case.append(
            {
                "case_id": case_id,
                "b": {
                    "business_ok": bool(b_result.get("business_ok")),
                    "oracle_passed": bool(b_result.get("passed")),
                    "unconfirmed_write": bool(b_result.get("policy_violation")),
                },
                "c": {
                    "business_ok": bool((c_result or {}).get("business_ok")),
                    "oracle_passed": bool((c_result or {}).get("passed")),
                    "unconfirmed_write": bool((c_result or {}).get("policy_violation")),
                },
                "missing_in_c": c_result is None,
            }
        )
    return {
        "baselines": {
            "b": {"name": "Rule-Guarded", **_aggregate(b_results)},
            "c": {"name": "Reliable Agent", **_aggregate(c_results)},
        },
        "cases": per_case,
    }


def render_comparison_markdown(comparison: dict) -> str:
    """生成对照报告；数字全部来自本次执行，不写死。"""
    b = comparison["baselines"]["b"]
    c = comparison["baselines"]["c"]
    b_business = b["business_success_rate"] * 100
    c_business = c["business_success_rate"] * 100
    b_oracle = b["oracle_pass_rate"] * 100
    c_oracle = c["oracle_pass_rate"] * 100
    b_unconfirmed = b["unconfirmed_write_rate"] * 100
    c_unconfirmed = c["unconfirmed_write_rate"] * 100
    lines = [
        "# A/B/C 对照实验（当前仅 B、C 两档）",
        "",
        "A 档（无授权、无规则）不复现：生产代码中不存在跳过用户归属过滤的入口，"
        "为对照实验人为制造一条旁路，会让仓库长期保留一条可被误用的路径。"
        "因此本报告只能量化「确认、幂等与恢复」的收益，不能量化授权与业务规则本身的收益。",
        "",
        "## 汇总",
        "",
        "| 指标 | B · Rule-Guarded | C · Reliable Agent |",
        "|---|---:|---:|",
        f"| 业务结果达成率 | {b_business:.1f}% | {c_business:.1f}% |",
        f"| Oracle 通过率 | {b_oracle:.1f}% | {c_oracle:.1f}% |",
        f"| 未确认写入率 | {b_unconfirmed:.1f}% | {c_unconfirmed:.1f}% |",
        f"| 用例数 | {b['cases']} | {c['cases']} |",
        "",
        "## 逐条结果",
        "",
        "| 用例 | B 业务达成 | B 未确认写入 | C 业务达成 | C 未确认写入 |",
        "|---|---|---|---|---|",
    ]
    for case in comparison["cases"]:
        b_case, c_case = case["b"], case["c"]
        lines.append(
            f"| `{case['case_id']}` | {_mark(b_case['business_ok'])} | "
            f"{_mark(b_case['unconfirmed_write'])} | {_mark(c_case['business_ok'])} | "
            f"{_mark(c_case['unconfirmed_write'])} |"
        )
    lines.extend(
        [
            "",
            "## 结论",
            "",
            "- 业务结果达成率无法区分两档：B 在没有确认的情况下同样能完成取消。"
            "差别不在结果，而在用户是否被询问过。",
            "- 未确认写入率是本次实验唯一能区分两档的指标：B 的每一笔成功取消都对应一条"
            "没有确认记录的写操作，C 全部有。",
            "- 本次未覆盖重复写入。B 档仍受 `refund_requests` 上 `kind='cancellation'` "
            "唯一索引的约束，但服务端幂等层面的收益需要独立的重复提交用例才能量化。",
            "- B 无法写审计日志：`operation_logs` 对 `confirmations` 有复合外键，"
            "没有确认记录就写不进去。这不是实现遗漏，而是确认机制缺失的直接后果。",
            "",
            "生产 API 固定使用 C 档；B 档只存在于 `baselines/` 与评测入口。",
            "",
        ]
    )
    return "\n".join(lines)


def _mark(value: bool) -> str:
    return "是" if value else "否"