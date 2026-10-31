from uuid import uuid4
from app.agent.model_client import create_client
from app.harness.runtime import create_messages, run_agent
from app.agent.prompts import (
    ORDER_PROMPT, 
    POLICY_PROMPT, 
    REFUND_PROMPT,
    build_system_prompt
)

def main():
    client = create_client()

    # 构建当前run的系统提示词
    system_prompt = build_system_prompt(
        ORDER_PROMPT, 
        POLICY_PROMPT, 
        REFUND_PROMPT
    )
    messages = create_messages(system_prompt)
    # 本地演示身份；以后接入服务端会话提供的身份。
    user_id = "USER-001"
    # 一次启动对应一个本地演示会话和对话线程。
    session_id = f"sess_{uuid4().hex}"
    thread_id = f"thread_{uuid4().hex}"

    print(f"[会话] {session_id}")
    print(f"[线程] {thread_id}")

    print("订单助手已启动，可确认或拒绝待确认退款操作，输入 exit 退出。")

    while True:
        question = input("\n你：").strip()
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
        print(f"[当前历史消息数] {len(messages)}")


if __name__ == "__main__":
    main()
