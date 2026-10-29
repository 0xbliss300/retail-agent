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

    print("订单助手已启动，输入 exit 退出。")

    while True:
        question = input("\n你：").strip()
        if question.lower() == "exit":
            print("对话结束。")
            break
        if not question:
            continue

        answer = run_agent(question, user_id, messages, client)
        print("\n助手：", answer)
        print(f"[当前历史消息数] {len(messages)}")


if __name__ == "__main__":
    main()
