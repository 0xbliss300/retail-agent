"""启动入口恢复待确认操作；不保存或恢复完整模型消息历史。"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from openai.types.chat import ChatCompletionMessage

from app.business.service import approve_refund_confirmation
from app.harness.confirmation import prepare_refund_confirmation
from app.storage import database
from app.storage.seed import seed_orders
from scripts import demo_chat


@pytest.fixture
def resume_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retail.db")
    database.init_db()
    clock = {"now": datetime(2026, 10, 31, 2, tzinfo=timezone.utc)}

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = clock["now"]
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(demo_chat, "datetime", FixedDatetime, raising=False)
    monkeypatch.setattr("app.harness.runtime.datetime", FixedDatetime)

    def prepare(
        *, user_id="USER-001", order_id="ORD-RESUME",
        session_id="sess_resume_test", thread_id="thread_resume_test",
    ):
        conn = database.get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO orders (
                        order_id, user_id, status, paid_amount_cents, shipped_at
                    ) VALUES (?, ?, 'shipped', 8900, ?)
                    """,
                    (order_id, user_id, "2026-10-30T02:00:00+00:00"),
                )
        finally:
            conn.close()
        context = {
            "user_id": user_id,
            "session_id": session_id,
            "thread_id": thread_id,
        }
        result = prepare_refund_confirmation(
            **context, order_id=order_id, reason="商品破损", now=clock["now"],
        )
        assert result["status"] == "awaiting_confirmation"
        return {**context, "confirmation_id": result["confirmation_id"]}

    return prepare, clock


def resume_args(context):
    return ["--resume-session", context["session_id"], "--resume-thread", context["thread_id"]]


def read_state(confirmation_id):
    conn = database.get_connection()
    try:
        record = dict(conn.execute(
            "SELECT * FROM confirmations WHERE confirmation_id = ?", (confirmation_id,),
        ).fetchone())
        refunds = [dict(row) for row in conn.execute("SELECT * FROM refund_requests")]
        return record, refunds
    finally:
        conn.close()


@pytest.mark.parametrize(
    "intent,text,status,count",
    [("approve", "确认提交", "consumed", 1), ("reject", "算了不申请了", "rejected", 0)],
)
def test_reopen_and_decide_saved_operation(
    resume_db, monkeypatch, capsys, intent, text, status, count,
):
    prepare, _ = resume_db
    context = prepare()
    monkeypatch.setattr(demo_chat, "create_client", lambda: None)

    # 第一次恢复后直接退出，退出不能暗中提交或拒绝。
    monkeypatch.setattr("builtins.input", lambda prompt: "exit")
    demo_chat.main(resume_args(context))
    record, refunds = read_state(context["confirmation_id"])
    assert record["status"] == "pending"
    assert refunds == []

    response = ChatCompletionMessage(
        role="assistant", content=None,
        tool_calls=[{
            "id": "call_resume_intent", "type": "function",
            "function": {
                "name": "report_confirmation_intent",
                "arguments": json.dumps({"intent": intent, "evidence": text}),
            },
        }],
    )
    monkeypatch.setattr(
        "app.agent.confirmation_intent.request_model", lambda client, messages, tools: response,
    )
    inputs = iter([text, "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    demo_chat.main(resume_args(context))

    record, refunds = read_state(context["confirmation_id"])
    assert record["status"] == status
    assert len(refunds) == count
    output = capsys.readouterr().out
    assert context["confirmation_id"] in output
    assert "89.00" in output
    if count:
        assert refunds[0]["confirmation_id"] == context["confirmation_id"]
        assert refunds[0]["refund_id"] in output


def forbid_interaction(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("无法恢复时不应创建模型客户端或进入输入循环")
    monkeypatch.setattr(demo_chat, "create_client", forbidden)
    monkeypatch.setattr("builtins.input", forbidden)


@pytest.mark.parametrize("field", ["session_id", "thread_id"])
def test_wrong_context_does_not_resume(resume_db, monkeypatch, field):
    prepare, _ = resume_db
    context = prepare()
    before = read_state(context["confirmation_id"])
    forbid_interaction(monkeypatch)

    demo_chat.main(resume_args({**context, field: "wrong"}))

    assert read_state(context["confirmation_id"]) == before


def test_other_users_operation_does_not_resume(resume_db, monkeypatch):
    prepare, _ = resume_db
    context = prepare(user_id="USER-OTHER")
    before = read_state(context["confirmation_id"])
    forbid_interaction(monkeypatch)

    # 演示入口固定使用 USER-001，知道编号也不能恢复其他用户的操作。
    demo_chat.main(resume_args(context))

    assert read_state(context["confirmation_id"]) == before


def test_expired_operation_is_closed_without_model(resume_db, monkeypatch):
    prepare, clock = resume_db
    context = prepare()
    clock["now"] += timedelta(minutes=10)
    forbid_interaction(monkeypatch)

    demo_chat.main(resume_args(context))

    record, refunds = read_state(context["confirmation_id"])
    assert record["status"] == "expired"
    assert refunds == []


def test_consumed_operation_is_not_executed_again(resume_db, monkeypatch):
    prepare, clock = resume_db
    context = prepare()
    result = approve_refund_confirmation(**context, now=clock["now"])
    assert result["ok"] is True
    before = read_state(context["confirmation_id"])
    forbid_interaction(monkeypatch)

    demo_chat.main(resume_args(context))

    assert read_state(context["confirmation_id"]) == before


@pytest.mark.parametrize("args", [["--resume-session", "s"], ["--resume-thread", "t"]])
def test_resume_requires_both_identifiers(monkeypatch, args):
    forbid_interaction(monkeypatch)
    with pytest.raises(SystemExit) as error:
        demo_chat.main(args)
    assert error.value.code == 2


def test_resume_demo_order_seed_preserves_existing_data(resume_db):
    seed_orders()
    conn = database.get_connection()
    try:
        row = conn.execute("SELECT * FROM orders WHERE order_id = 'ORD-2003'").fetchone()
        assert row is not None
        assert row["user_id"] == "USER-001"
        assert row["status"] == "shipped"
        assert row["paid_amount_cents"] == 6900
        with conn:
            conn.execute("UPDATE orders SET status = 'canceled' WHERE order_id = 'ORD-2003'")
        before = list(conn.iterdump())
        seed_orders()
        assert list(conn.iterdump()) == before
    finally:
        conn.close()


def install_menu_interaction(monkeypatch, inputs, *, intent="approve", text="确认提交"):
    answers = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(demo_chat, "create_client", lambda: None)
    response = ChatCompletionMessage(
        role="assistant", content=None,
        tool_calls=[{
            "id": "call_menu_intent", "type": "function",
            "function": {
                "name": "report_confirmation_intent",
                "arguments": json.dumps({"intent": intent, "evidence": text}),
            },
        }],
    )
    monkeypatch.setattr(
        "app.agent.confirmation_intent.request_model", lambda client, messages, tools: response,
    )

    def no_ordinary_model(*args, **kwargs):
        raise AssertionError("选择操作时不能将菜单输入发送给普通模型")
    monkeypatch.setattr("app.harness.runtime.request_model", no_ordinary_model)


def test_normal_start_lists_owned_operation_and_resumes_without_ids(resume_db, monkeypatch, capsys):
    prepare, _ = resume_db
    context = prepare()
    install_menu_interaction(monkeypatch, ["1", "确认提交", "exit"])

    demo_chat.main([])

    record, refunds = read_state(context["confirmation_id"])
    assert record["status"] == "consumed"
    assert len(refunds) == 1
    assert refunds[0]["confirmation_id"] == context["confirmation_id"]
    output = capsys.readouterr().out
    assert "ORD-RESUME" in output
    assert "89.00" in output
    assert "商品破损" in output
    assert record["expires_at"] in output


def test_menu_reprompts_invalid_choice_without_execution(resume_db, monkeypatch):
    prepare, _ = resume_db
    context = prepare()
    install_menu_interaction(monkeypatch, ["bad", "99", "1", "exit"])

    demo_chat.main([])

    record, refunds = read_state(context["confirmation_id"])
    assert record["status"] == "pending"
    assert refunds == []


def test_menu_new_conversation_does_not_consume_pending_operation(resume_db, monkeypatch):
    prepare, _ = resume_db
    context = prepare()
    before = read_state(context["confirmation_id"])
    install_menu_interaction(monkeypatch, ["0", "exit"])

    demo_chat.main([])

    assert read_state(context["confirmation_id"]) == before


def test_menu_only_displays_current_users_operations(resume_db, monkeypatch, capsys):
    prepare, _ = resume_db
    own = prepare()
    other = prepare(
        user_id="USER-OTHER", order_id="ORD-PRIVATE",
        session_id="sess_private", thread_id="thread_private",
    )
    before = read_state(other["confirmation_id"])
    install_menu_interaction(monkeypatch, ["1", "exit"])

    demo_chat.main([])

    output = capsys.readouterr().out
    assert "ORD-RESUME" in output
    assert "ORD-PRIVATE" not in output
    assert read_state(other["confirmation_id"]) == before
    assert read_state(own["confirmation_id"])[0]["status"] == "pending"


def test_menu_expires_own_operations_before_listing(resume_db, monkeypatch, capsys):
    prepare, clock = resume_db
    own = prepare()
    other = prepare(
        user_id="USER-OTHER", order_id="ORD-PRIVATE",
        session_id="sess_private", thread_id="thread_private",
    )
    clock["now"] += timedelta(minutes=10)
    install_menu_interaction(monkeypatch, ["exit"])

    demo_chat.main([])

    assert read_state(own["confirmation_id"])[0]["status"] == "expired"
    assert read_state(other["confirmation_id"])[0]["status"] == "pending"
    assert "ORD-RESUME" not in capsys.readouterr().out


def test_menu_selection_preserves_the_other_pending_operation(resume_db, monkeypatch):
    prepare, _ = resume_db
    first = prepare()
    second = prepare(
        order_id="ORD-SECOND", session_id="sess_second", thread_id="thread_second",
    )
    # 创建时间相同：后插入的第二条显示为1，第一条显示为2。
    install_menu_interaction(
        monkeypatch, ["2", "算了不申请了", "exit"], intent="reject", text="算了不申请了",
    )

    demo_chat.main([])

    assert read_state(first["confirmation_id"])[0]["status"] == "rejected"
    assert read_state(second["confirmation_id"])[0]["status"] == "pending"
    assert read_state(first["confirmation_id"])[1] == []


def test_menu_rechecks_expiry_after_user_selection(resume_db, monkeypatch):
    prepare, clock = resume_db
    context = prepare()

    def select_after_expiry(prompt):
        clock["now"] += timedelta(minutes=10)
        return "1"

    forbid_interaction(monkeypatch)
    monkeypatch.setattr("builtins.input", select_after_expiry)

    demo_chat.main([])

    record, refunds = read_state(context["confirmation_id"])
    assert record["status"] == "expired"
    assert refunds == []
