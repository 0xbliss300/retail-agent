"""本地聊天入口：按业务摘要选择并继续待确认的退款操作。"""

import argparse
from datetime import datetime, timezone
from uuid import uuid4

from app.agent.model_client import create_client
from app.harness.confirmation import (
    list_resumable_confirmations,
    load_active_confirmation,
)
from app.harness.runtime import create_messages, run_agent
from app.agent.prompts import (
    ORDER_PROMPT,
    POLICY_PROMPT,
    REFUND_PROMPT,
    build_system_prompt,
)


def choose_pending_confirmation(user_id: str) -> dict | None:
    """展示本人未过期的操作；序号选择不触发批准或拒绝。"""
    records = list_resumable_confirmations(
        user_id=user_id,
        now=datetime.now(timezone.utc),
    )
    if not records:
        return None

    print("你有未完成的退款操作：")
    for number, record in enumerate(records, start=1):
        print(f"{number}. {record['summary']}")
        print(f"   有效期至：{record['expires_at']}")
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
    if args.resume_session is None:
        try:
            selected = choose_pending_confirmation(user_id)
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

    if resume:
        # 选择菜单可能耗时，也可能已有其他入口处理了操作，必须重新读取。
        active = load_active_confirmation(
            user_id=user_id,
            session_id=session_id,
            thread_id=thread_id,
            now=datetime.now(timezone.utc),
        )
        if active is None:
            print("所选操作不存在、已结束或已过期，未执行退款申请。")
            return
        if active["status"] != "pending":
            print("该操作不处于待确认状态，当前入口无法恢复。")
            return

    system_prompt = build_system_prompt(
        ORDER_PROMPT,
        POLICY_PROMPT,
        REFUND_PROMPT,
    )
    messages = create_messages(system_prompt)
    if active is not None:
        print("已恢复待确认的退款操作，历史聊天记录未加载。")
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


if __name__ == "__main__":
    main()
