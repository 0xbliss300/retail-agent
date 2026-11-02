"""调用本地 SSE 流式接口并打印文本。"""

import argparse
import json

import httpx


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="本地流式聊天演示")
    parser.add_argument("--message", default="查询我的订单", help="本轮用户消息")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--session-id", help="继续已有对话时提供")
    parser.add_argument("--thread-id", help="继续已有对话时提供")
    args = parser.parse_args(argv)

    if (args.session_id is None) != (args.thread_id is None):
        parser.error("--session-id 和 --thread-id 必须一起提供")

    payload = {"message": args.message}
    if args.session_id is not None:
        payload["session_id"] = args.session_id
        payload["thread_id"] = args.thread_id

    try:
        with httpx.stream(
            "POST",
            f"{args.base_url}/chat/stream",
            json=payload,
            timeout=120.0,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])
                if "text" in data:
                    print(data["text"], end="", flush=True)
                elif "reply" in data or "error" in data:
                    print("\n")
                    print(json.dumps(data, ensure_ascii=False, indent=2))
    except httpx.HTTPStatusError as exc:
        print(exc.response.text)
    except httpx.HTTPError as exc:
        print(f"请求流式接口失败：{exc}")


if __name__ == "__main__":
    main()