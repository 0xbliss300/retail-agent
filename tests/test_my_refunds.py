"""本人退款申请列表：归属隔离、状态筛选、分页与只读性。"""

import pytest
from openai.types.chat import ChatCompletionMessage

from app.business import service
from app.harness.tool_executor import execute_tool
from app.storage import database


def dump_database():
    conn = database.get_connection()
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def model_call(name, arguments):
    return ChatCompletionMessage(
        role="assistant", content=None,
        tool_calls=[{
            "id": f"call_{name}", "type": "function",
            "function": {"name": name, "arguments": arguments},
        }],
    )


@pytest.fixture
def refunds(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()
    conn = database.get_connection()
    try:
        with conn:
            conn.executemany(
                """
                INSERT INTO orders (order_id,user_id,status,paid_amount_cents,shipped_at)
                VALUES (?,?,?,?,?)
                """,
                [
                    ("ORD-A", "USER-001", "shipped", 12900, "2026-10-26T02:00:00+00:00"),
                    ("ORD-B", "USER-001", "shipped", 8900, "2026-10-26T02:00:00+00:00"),
                    ("ORD-C", "USER-001", "refunded", 6900, "2026-10-26T02:00:00+00:00"),
                    ("ORD-D", "USER-001", "canceled", 0, None),
                    ("ORD-PRIVATE", "USER-002", "shipped", 99900, "2026-10-26T02:00:00+00:00"),
                ],
            )
            conn.executemany(
                """
                INSERT INTO refund_requests (
                    refund_id, order_id, user_id, kind, amount_cents, reason,
                    status, idempotency_key, confirmation_id, created_at, updated_at
                )
                VALUES (?,?,?,?,?,?,?,NULL,NULL,?,?)
                """,
                [
                    ("rf-1", "ORD-A", "USER-001", "after_sales", 12900, "商品破损",
                     "pending", "2026-10-27T02:00:00+00:00", "2026-10-27T02:00:00+00:00"),
                    ("rf-2", "ORD-B", "USER-001", "after_sales", 8900, "不想要了",
                     "withdrawn", "2026-10-28T02:00:00+00:00", "2026-10-28T03:00:00+00:00"),
                    ("rf-3", "ORD-C", "USER-001", "after_sales", 6900, "质量问题",
                     "completed", "2026-10-29T02:00:00+00:00", "2026-10-29T05:00:00+00:00"),
                    # 取消产生的退款流水不属于用户提交的售后申请。
                    ("rf-4", "ORD-D", "USER-001", "cancellation", 0, "取消订单",
                     "completed", "2026-10-30T02:00:00+00:00", "2026-10-30T02:00:00+00:00"),
                    ("rf-5", "ORD-PRIVATE", "USER-002", "after_sales", 99900, "商品破损",
                     "pending", "2026-10-30T02:00:00+00:00", "2026-10-30T02:00:00+00:00"),
                ],
            )
    finally:
        conn.close()

    def query(arguments="{}", *, user_id="USER-001"):
        before = dump_database()
        result = execute_tool(
            model_call("list_refunds", arguments).tool_calls[0], user_id,
        )
        assert dump_database() == before
        return result

    return query


def test_lists_own_after_sales_refunds_newest_first(refunds):
    result = refunds()

    assert result["ok"] is True
    assert [row["refund_id"] for row in result["refunds"]] == ["rf-3", "rf-2", "rf-1"]
    assert result["refunds"][0] == {
        "refund_id": "rf-3", "order_id": "ORD-C", "amount_cents": 6900,
        "status": "completed", "reason": "质量问题",
        "created_at": "2026-10-29T02:00:00+00:00",
        "updated_at": "2026-10-29T05:00:00+00:00",
        "order_status": "refunded",
    }
    assert all("user_id" not in row for row in result["refunds"])
    assert result["status"] is None
    assert result["has_more"] is False
    assert result["next_offset"] is None


def test_excludes_cancellation_refunds(refunds):
    result = refunds()

    assert "rf-4" not in [row["refund_id"] for row in result["refunds"]]


def test_filters_by_status(refunds):
    result = refunds('{"status": "pending"}')

    assert [row["refund_id"] for row in result["refunds"]] == ["rf-1"]
    assert result["status"] == "pending"
    assert result["has_more"] is False


def test_paginates_with_next_offset(refunds):
    first = refunds('{"limit": 2}')
    assert [row["refund_id"] for row in first["refunds"]] == ["rf-3", "rf-2"]
    assert first["has_more"] is True
    assert first["next_offset"] == 2

    second = refunds('{"limit": 2, "offset": 2}')
    assert [row["refund_id"] for row in second["refunds"]] == ["rf-1"]
    assert second["has_more"] is False
    assert second["next_offset"] is None


def test_cannot_see_other_users_refunds(refunds):
    result = refunds(user_id="USER-002")

    assert [row["refund_id"] for row in result["refunds"]] == ["rf-5"]
    assert all(row["order_id"] != "ORD-A" for row in result["refunds"])


def test_empty_list_for_user_without_refunds(refunds):
    result = refunds(user_id="USER-EMPTY")

    assert result["ok"] is True
    assert result["refunds"] == []
    assert result["has_more"] is False


@pytest.mark.parametrize(
    "arguments",
    [
        '{"status": "unknown"}',
        '{"status": 1}',
        '{"limit": 0}',
        '{"limit": 51}',
        '{"limit": "10"}',
        '{"offset": -1}',
        '{"user_id": "USER-002"}',
        '{"kind": "cancellation"}',
    ],
)
def test_rejects_invalid_arguments_without_writing(refunds, arguments):
    result = refunds(arguments)

    assert result["ok"] is False
    assert result["error"] == "INVALID_ARGUMENTS"


def test_service_checks_boundaries_directly(refunds):
    assert service.list_refunds(user_id="USER-001", status="bogus")["error"] == "INVALID_ARGUMENTS"
    assert service.list_refunds(user_id="  ")["error"] == "MISSING_CONTEXT"
    assert service.list_refunds(user_id="USER-001", status=["pending"])["error"] == "INVALID_ARGUMENTS"
