"""用临时 SQLite 和离线模型演示一次取消响应丢失。"""

import asyncio
import json
import os
import re
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from fastapi import Response
from openai.types.chat import ChatCompletionMessage

from app import main
from app.harness import runtime
from app.storage import database
from app.storage.seed import seed_cancellation_orders, seed_demo_accounts


def offline_model(client, messages, tools):
    order = re.search(r"\bORD-300[12]\b", messages[-1]["content"], re.IGNORECASE)
    if order is None:
        yield "message", ChatCompletionMessage(role="assistant", content="请输入：取消 ORD-3002，不需要了。")
        return
    yield "message", ChatCompletionMessage(
        role="assistant",
        tool_calls=[{
            "id": "demo_cancel",
            "type": "function",
            "function": {
                "name": "cancel_order",
                "arguments": json.dumps({"order_id": order[0].upper(), "reason": "不需要了"}),
            },
        }],
    )


def run_demo() -> None:
    if not main.FRONTEND_ASSETS.is_dir():
        raise SystemExit("请先运行 npm --prefix frontend run build")

    os.environ["TRACE_ENABLED"] = "false"
    main.create_client = lambda: None
    runtime.stream_model = offline_model
    dropped = False

    @main.app.middleware("http")
    async def drop_first_paid_cancel_response(request, call_next):
        nonlocal dropped
        response = await call_next(request)
        if dropped or request.url.path != "/confirm" or response.status_code != 200:
            return response
        with closing(database.get_connection()) as conn:
            order = conn.execute("SELECT status FROM orders WHERE order_id = 'ORD-3002'").fetchone()
            refunds = conn.execute("""
                SELECT refund_id FROM refund_requests
                WHERE order_id = 'ORD-3002' AND kind = 'cancellation'
                """).fetchall()
        if order["status"] != "canceled":
            return response

        dropped = True
        print(f"ORD-3002 {order['status']}，模拟退款流水 {len(refunds)} 条", flush=True)
        for refund in refunds:
            print(f"模拟退款流水号 {refund['refund_id']}", flush=True)
        # 原响应停在演示服务里，等网页超时或刷新断开连接。
        await asyncio.sleep(12)
        return Response(status_code=504)

    with TemporaryDirectory(prefix="retail-cancel-recovery-") as temp:
        database.DB_PATH = Path(temp) / "retail.db"
        database.init_db()
        seed_demo_accounts()
        seed_cancellation_orders()
        uvicorn.run(main.app, host="127.0.0.1", port=8765)


if __name__ == "__main__":
    run_demo()
