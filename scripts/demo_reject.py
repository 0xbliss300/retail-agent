"""演示拒绝和到期关闭；每次运行新增两条终态模拟确认记录。"""

import json
from datetime import datetime, timedelta
from uuid import uuid4

from app.harness.confirmation import prepare_refund_confirmation, reject_confirmation
from app.storage.database import get_connection


def show(label: str, result) -> None:
    print(label, json.dumps(result, ensure_ascii=True))


def read_business_state() -> dict:
    conn = get_connection()
    try:
        return {
            "orders": [dict(row) for row in conn.execute("SELECT * FROM orders ORDER BY order_id")],
            "refunds": [dict(row) for row in conn.execute("SELECT * FROM refund_requests ORDER BY refund_id")],
        }
    finally:
        conn.close()


def main() -> None:
    # 固定演示时钟，保证不会因实际运行日期改变而超过退款窗口。
    now = datetime.fromisoformat("2026-10-29T02:00:00+00:00")
    context = {
        "user_id": "USER-001",
        "session_id": f"sess_demo_reject_{uuid4().hex}",
        "thread_id": f"thread_demo_reject_{uuid4().hex}",
    }
    before = read_business_state()
    pending = prepare_refund_confirmation(
        **context, order_id="ORD-2002", reason="模拟：商品破损", now=now,
    )
    show("prepare", pending)
    if pending.get("status") != "awaiting_confirmation":
        raise RuntimeError("未创建待确认操作，请检查模拟订单及已有申请")

    confirmation_id = pending["confirmation_id"]
    for field in ("user_id", "session_id", "thread_id"):
        wrong_context = {**context, field: "wrong_context"}
        show(f"wrong_{field}", reject_confirmation(
            **wrong_context, confirmation_id=confirmation_id, now=now,
        ))

    for label in ("reject", "repeat_reject"):
        show(label, reject_confirmation(
            **context, confirmation_id=confirmation_id, now=now + timedelta(minutes=1),
        ))

    # 拒绝后该线程已释放，可以准备下一次操作。
    expiring = prepare_refund_confirmation(
        **context, order_id="ORD-2002", reason="模拟：到期关闭", now=now,
    )
    show("prepare_again", expiring)
    if expiring.get("status") != "awaiting_confirmation":
        raise RuntimeError("拒绝后未能创建新的待确认操作")
    for label in ("at_expiry", "repeat_expired"):
        show(label, reject_confirmation(
            **context, confirmation_id=expiring["confirmation_id"],
            now=now + timedelta(minutes=10),
        ))

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT confirmation_id, status FROM confirmations
            WHERE user_id = ? AND session_id = ? AND thread_id = ?
            ORDER BY confirmation_id
            """,
            (context["user_id"], context["session_id"], context["thread_id"]),
        ).fetchall()
        show("saved_states", [dict(row) for row in rows])
    finally:
        conn.close()
    after = read_business_state()
    show("business_data_unchanged", before == after)
    show("refund_count", len(after["refunds"]))


if __name__ == "__main__":
    main()
