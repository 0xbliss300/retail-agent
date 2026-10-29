"""使用临时 SQLite 验证只读工具的结果、隔离与零写入。"""

import tempfile
from pathlib import Path

import pytest

from app.business.tools import get_order, get_policy
from app.storage import database


@pytest.fixture
def readonly_database(monkeypatch):
    temp_dir = tempfile.TemporaryDirectory()
    monkeypatch.setattr(database, "DB_PATH", Path(temp_dir.name) / "retail.db")
    database.init_db()
    conn = database.get_connection()
    try:
        with conn:
            conn.execute(
                "INSERT INTO orders (order_id, user_id, status, paid_amount_cents) "
                "VALUES (?, ?, ?, ?)",
                ("ORD-TEST", "USER-TEST", "paid", 9900),
            )
        before = list(conn.iterdump())
        yield
        assert list(conn.iterdump()) == before
    finally:
        conn.close()
        temp_dir.cleanup()


def test_get_order_returns_owned_order(readonly_database):
    result = get_order("USER-TEST", "ORD-TEST")
    assert result == {
        "ok": True,
        "order": {
            "order_id": "ORD-TEST",
            "user_id": "USER-TEST",
            "status": "paid",
            "paid_amount_cents": 9900,
            "paid_at": None,
            "shipped_at": None,
            "delivered_at": None,
        },
    }


@pytest.mark.parametrize("user_id,order_id", [
    ("OTHER-USER", "ORD-TEST"),
    ("USER-TEST", "ORD-MISSING"),
])
def test_get_order_hides_inaccessible_orders(readonly_database, user_id, order_id):
    assert get_order(user_id, order_id) == {"ok": False, "error": "ORDER_NOT_FOUND"}


@pytest.mark.parametrize("topic,expected_text", [
    ("cancellation", "取消订单需要用户确认。"),
    ("refund", "申请提交成功不代表退款已经到账。"),
])
def test_get_policy_returns_general_policy(readonly_database, topic, expected_text):
    result = get_policy(topic)
    assert result["ok"] is True
    assert result["topic"] == topic
    assert expected_text in result["policy"]


def test_get_policy_rejects_unknown_topic(readonly_database):
    assert get_policy("unknown") == {"ok": False, "error": "POLICY_NOT_FOUND"}
