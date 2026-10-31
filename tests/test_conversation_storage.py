"""完整轮次快照的持久化、迁移和用户隔离。"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.storage import database, persistence


@pytest.fixture
def conversation_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()
    return datetime(2026, 10, 31, 2, tzinfo=timezone.utc)


def history():
    return [
        {"role": "system", "content": "根据查询结果回答。"},
        {"role": "user", "content": "查询 ORD-1001"},
        {
            "role": "assistant", "content": None,
            "tool_calls": [{
                "id": "call_order", "type": "function",
                "function": {"name": "get_order", "arguments": '{"order_id":"ORD-1001"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call_order", "content": '{"ok":true}'},
        {"role": "assistant", "content": "订单已支付。"},
    ]


CONTEXT = {"user_id": "USER-001", "session_id": "sess_history", "thread_id": "thread_history"}


def business_state():
    conn = database.get_connection()
    try:
        return {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("orders", "refund_requests", "confirmations")
        }
    finally:
        conn.close()


def test_initialization_creates_conversation_table(conversation_db):
    conn = database.get_connection()
    try:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chat_threads'"
        ).fetchone() is not None
    finally:
        conn.close()


def test_snapshot_survives_closed_connection_with_tool_messages(conversation_db):
    messages = history()
    original = deepcopy(messages)
    before = business_state()

    version = persistence.save_conversation(
        **CONTEXT, messages=messages, now=conversation_db, expected_version=0,
    )
    loaded = persistence.load_conversation(**CONTEXT)

    assert version == 1
    assert loaded["version"] == 1
    assert loaded["title"] == "查询 ORD-1001"
    assert loaded["messages"] == original
    assert messages == original
    assert business_state() == before


def test_snapshot_update_preserves_creation_time(conversation_db):
    messages = history()
    persistence.save_conversation(**CONTEXT, messages=messages, now=conversation_db, expected_version=0)
    messages.extend([
        {"role": "user", "content": "这个订单多少钱"},
        {"role": "assistant", "content": "99元。"},
    ])

    version = persistence.save_conversation(
        **CONTEXT, messages=messages, now=conversation_db + timedelta(minutes=1), expected_version=1,
    )
    loaded = persistence.load_conversation(**CONTEXT)

    assert version == 2
    assert loaded["messages"] == messages
    assert loaded["created_at"] == "2026-10-31T02:00:00+00:00"
    assert loaded["updated_at"] == "2026-10-31T02:01:00+00:00"


@pytest.mark.parametrize("field", ["user_id", "session_id", "thread_id"])
def test_snapshot_is_only_loaded_for_matching_context(conversation_db, field):
    persistence.save_conversation(**CONTEXT, messages=history(), now=conversation_db, expected_version=0)
    assert persistence.load_conversation(**{**CONTEXT, field: "wrong"}) is None


@pytest.mark.parametrize("stale_version", [0, 1])
def test_stale_save_cannot_overwrite_newer_history(conversation_db, stale_version):
    old = history()
    persistence.save_conversation(**CONTEXT, messages=old, now=conversation_db, expected_version=0)
    updated = old + [
        {"role": "user", "content": "继续查退款进度"},
        {"role": "assistant", "content": "尚无申请。"},
    ]
    persistence.save_conversation(**CONTEXT, messages=updated, now=conversation_db, expected_version=1)
    before = persistence.load_conversation(**CONTEXT)

    with pytest.raises(persistence.ConversationConflict):
        persistence.save_conversation(
            **CONTEXT, messages=old, now=conversation_db, expected_version=stale_version,
        )

    assert persistence.load_conversation(**CONTEXT) == before


def test_unknown_conversation_does_not_create_on_update(conversation_db):
    with pytest.raises(persistence.ConversationConflict):
        persistence.save_conversation(
            **CONTEXT, messages=history(), now=conversation_db, expected_version=1,
        )
    assert persistence.load_conversation(**CONTEXT) is None


def test_reinitialization_keeps_saved_history_and_business_data(conversation_db):
    persistence.save_conversation(**CONTEXT, messages=history(), now=conversation_db, expected_version=0)
    saved = persistence.load_conversation(**CONTEXT)
    before = business_state()

    database.init_db()
    database.init_db()

    assert persistence.load_conversation(**CONTEXT) == saved
    assert business_state() == before


@pytest.mark.parametrize("ending", [
    {"role": "user", "content": "尚未回答的问题"},
    {"role": "assistant", "content": None, "tool_calls": [{"id": "not_finished"}]},
])
def test_unfinished_turn_is_not_saved(conversation_db, ending):
    messages = history() + [ending]
    with pytest.raises(ValueError):
        persistence.save_conversation(
            **CONTEXT, messages=messages, now=conversation_db, expected_version=0,
        )
    assert persistence.load_conversation(**CONTEXT) is None
