"""独立 Oracle：只读 SQLite，判定取消结果和禁止副作用。"""

import json
import sqlite3
from pathlib import Path

from eval.case_schema import EvalCase


def _check(name: str, actual, expected) -> dict:
    return {
        "name": name,
        "ok": actual == expected,
        "actual": actual,
        "expected": expected,
    }


def evaluate(db_path: Path, case: EvalCase) -> dict:
    """直接读取数据库，不调用 Agent、业务 Service 或模型。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        checks = []

        order = conn.execute(
            "SELECT * FROM orders WHERE order_id = ? AND user_id = ?",
            (case.order["order_id"], case.user_id),
        ).fetchone()
        checks.append(
            _check(
                "order.status",
                order["status"] if order is not None else None,
                case.expected["order_status"],
            )
        )
        checks.append(
            _check(
                "order.paid_amount_cents",
                order["paid_amount_cents"] if order is not None else None,
                case.order["paid_amount_cents"],
            )
        )

        refunds = conn.execute(
            "SELECT * FROM refund_requests WHERE order_id = ? AND user_id = ?",
            (case.order["order_id"], case.user_id),
        ).fetchall()
        cancellations = [row for row in refunds if row["kind"] == "cancellation"]
        after_sales = [row for row in refunds if row["kind"] == "after_sales"]
        refund = cancellations[0] if len(cancellations) == 1 else None
        checks.append(_check("refund.count", len(cancellations), 1))
        checks.append(
            _check(
                "refund.kind",
                refund["kind"] if refund is not None else None,
                case.expected["refund_kind"],
            )
        )
        checks.append(
            _check(
                "refund.amount_cents",
                refund["amount_cents"] if refund is not None else None,
                case.expected["refund_amount_cents"],
            )
        )
        checks.append(
            _check(
                "refund.status",
                refund["status"] if refund is not None else None,
                case.expected["refund_status"],
            )
        )
        checks.append(_check("refund.after_sales_count", len(after_sales), 0))

        confirmations = conn.execute(
            "SELECT * FROM confirmations WHERE order_id = ? AND user_id = ?",
            (case.order["order_id"], case.user_id),
        ).fetchall()
        confirmation = confirmations[0] if len(confirmations) == 1 else None
        checks.append(_check("confirmation.count", len(confirmations), 1))
        checks.append(
            _check(
                "confirmation.status",
                confirmation["status"] if confirmation is not None else None,
                case.expected["confirmation_status"],
            )
        )

        saved_status = None
        if confirmation is not None:
            try:
                saved = json.loads(confirmation["result_json"])
            except (TypeError, ValueError):
                saved = None
            if isinstance(saved, dict):
                saved_status = saved.get("status")
        checks.append(_check("confirmation.result_status", saved_status, "canceled"))

        logs = conn.execute(
            "SELECT * FROM operation_logs WHERE order_id = ? AND user_id = ?",
            (case.order["order_id"], case.user_id),
        ).fetchall()
        log = logs[0] if len(logs) == 1 else None
        checks.append(_check("operation_logs.count", len(logs), case.expected["operation_logs"]))
        checks.append(
            _check(
                "operation_logs.previous_status",
                log["previous_status"] if log is not None else None,
                case.order["status"],
            )
        )
        checks.append(
            _check(
                "operation_logs.new_status",
                log["new_status"] if log is not None else None,
                case.expected["order_status"],
            )
        )
        checks.append(
            _check(
                "operation_logs.amount_cents",
                log["amount_cents"] if log is not None else None,
                case.expected["refund_amount_cents"],
            )
        )

        other_canceled = conn.execute(
            """
            SELECT COUNT(*) FROM orders
            WHERE user_id = ? AND order_id <> ? AND status = 'canceled'
            """,
            (case.user_id, case.order["order_id"]),
        ).fetchone()[0]
        checks.append(_check("other_orders.canceled", other_canceled, 0))

        return {
            "passed": all(check["ok"] for check in checks),
            "checks": checks,
        }
    finally:
        conn.close()