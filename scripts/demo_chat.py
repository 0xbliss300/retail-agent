"""本地聊天入口：按标题恢复历史对话，并处理待确认的退款操作。"""

import argparse
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from app.agent.model_client import create_client
from app.agent.prompts import (
    ORDER_PROMPT,
    POLICY_PROMPT,
    REFUND_PROMPT,
    build_system_prompt,
)
from app.harness.confirmation import (
    list_resumable_confirmations,
    load_active_confirmation,
)
from app.harness.runtime import create_messages, run_agent
from app.storage.persistence import (
    ConversationConflict,
    list_conversations,
    load_conversation,
    save_conversation,
)


def choose_conversation(user_id: str) -> dict | None:
    """合并本人历史与待确认操作；菜单选择不调用模型或执行业务。"""
    pending = list_resumable_confirmations(
        user_id=user_id,
        now=datetime.now(UTC),
    )
    histories = {
        (row["session_id"], row["thread_id"]): row
        for row in list_conversations(user_id=user_id)
    }
    records = []
    # 待确认操作优先展示；同一上下文的历史合并到同一个菜单项。
    for operation in pending:
        key = (operation["session_id"], operation["thread_id"])
        history = histories.pop(key, None)
        records.append({
            "session_id": operation["session_id"],
            "thread_id": operation["thread_id"],
            "title": history["title"] if history is not None else operation["summary"],
            "pending": operation,
        })
    records.extend({**history, "pending": None} for history in histories.values())
    if not records:
        return None

    print("选择要继续的对话：")
    for number, record in enumerate(records, start=1):
        operation = record["pending"]
        marker = " [待确认退款]" if operation is not None else ""
        print(f"{number}. {record['title']}{marker}")
        if operation is not None:
            if record["title"] != operation["summary"]:
                print(f"   {operation['summary']}")
            print(f"   有效期至：{operation['expires_at']}")
        else:
            print(f"   最近更新：{record['updated_at']}")
    print("0. 开始新对话")

    while True:
        choice = input("请选择序号，或输入 exit 退出：").strip()
        if choice.lower() == "exit":
            raise SystemExit(0)
        try:
            index = int(choice)
        except ValueError:
            print("请选择列表中的有效序号。")
            continue
        if index == 0:
            return None
        if 1 <= index <= len(records):
            return records[index - 1]
        print("请选择列表中的有效序号。")


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="本地售后聊天演示")
    # 旧参数仅供开发调试，普通用户通过菜单选择操作。
    parser.add_argument("--resume-session", help=argparse.SUPPRESS)
    parser.add_argument("--resume-thread", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if (args.resume_session is None) != (args.resume_thread is None):
        parser.error("--resume-session 和 --resume-thread 必须一起提供")

    # 本地模拟身份，不接受模型或恢复参数传入用户身份。
    user_id = "USER-001"
    active = None
    resume = False
    selected = None
    if args.resume_session is None:
        try:
            selected = choose_conversation(user_id)
        except (EOFError, KeyboardInterrupt):
            print("\n对话结束。")
            return
        if selected is None:
            session_id = f"sess_{uuid4().hex}"
            thread_id = f"thread_{uuid4().hex}"
        else:
            session_id = selected["session_id"]
            thread_id = selected["thread_id"]
            resume = True
    else:
        session_id = args.resume_session.strip()
        thread_id = args.resume_thread.strip()
        if not session_id or not thread_id:
            parser.error("会话编号和线程编号不能为空")
        resume = True

    context = {
        "user_id": user_id,
        "session_id": session_id,
        "thread_id": thread_id,
    }
    try:
        snapshot = load_conversation(**context)
    except (sqlite3.Error, ValueError, TypeError):
        print("无法读取已保存的对话，原记录已保留。请检查数据后重试。")
        return

    if resume:
        # 选择菜单可能耗时，也可能已有其他入口处理了操作，必须重新读取。
        active = load_active_confirmation(
            user_id=user_id,
            session_id=session_id,
            thread_id=thread_id,
            now=datetime.now(UTC),
        )
        if active is None and snapshot is None:
            print("所选操作不存在、已结束或已过期，未执行退款申请。")
            return
        if active is not None and active["status"] != "pending":
            print("该操作不处于待确认状态，当前入口无法恢复。")
            return
        if active is None and selected is not None and selected["pending"] is not None:
            print("待确认操作已结束或已过期；仅恢复聊天历史，不执行退款申请。")

    system_prompt = build_system_prompt(
        ORDER_PROMPT,
        POLICY_PROMPT,
        REFUND_PROMPT,
    )
    version = snapshot["version"] if snapshot is not None else 0

    # 保留历史消息及工具调用关系，系统提示词使用当前程序版本。
    messages = create_messages(system_prompt)
    if snapshot is not None:
        messages.extend(snapshot["messages"][1:])
        print("已加载之前保存的对话消息。")
    if active is not None:
        print("已恢复待确认的退款操作。")
        print(f"确认编号：{active['confirmation_id']}")
        print(active["summary"])
        print(f"有效期至：{active['expires_at']}")
        print("你可以确认提交，或拒绝本次申请。")

    client = create_client()
    print("订单助手已启动，可确认或拒绝待确认退款操作，输入 exit 退出。")

    while True:
        try:
            question = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n对话结束。")
            break
        if question.lower() == "exit":
            print("对话结束。")
            break
        if not question:
            continue

        answer = run_agent(
            question,
            user_id,
            messages,
            client,
            session_id=session_id,
            thread_id=thread_id,
        )
        print("\n助手：", answer)

        # 业务操作可能已经提交；历史保存失败不能导致业务重试。
        try:
            version = save_conversation(
                **context,
                messages=messages,
                now=datetime.now(UTC),
                expected_version=version,
            )
        except ConversationConflict:
            print(
                "本轮历史未保存：对话已被其他窗口更新。"
                "当前聊天已停止，请重新打开。"
                "已执行的业务操作不会因此撤销。"
            )
            break
        except (sqlite3.Error, ValueError, TypeError):
            print(
                "本轮历史未保存，当前聊天已停止。"
                "请保留上方业务结果，并通过查询核对申请状态。"
                "请勿因历史保存失败而重复提交申请。"
            )
            break


if __name__ == "__main__":
    main()
