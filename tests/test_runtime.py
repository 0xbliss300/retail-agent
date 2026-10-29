"""离线验证运行时：模拟模型，使用独立的临时业务数据库。"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openai.types.chat import ChatCompletionMessage

from app.agent.prompts import ORDER_PROMPT, POLICY_PROMPT, build_system_prompt
from app.harness.runtime import create_messages, run_agent
from app.harness.tool_executor import execute_tool


def reply(content=None, arguments=None, name="get_order"):
    calls = None
    if arguments is not None:
        calls = [{"id": "call_test", "type": "function", "function": {
            "name": name, "arguments": arguments,
        }}]
    return ChatCompletionMessage(role="assistant", content=content, tool_calls=calls)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        db_path = Path(self.temp_dir.name) / "retail.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE orders (order_id TEXT PRIMARY KEY, user_id TEXT, "
                         "status TEXT, paid_amount_cents INTEGER, "
                         "paid_at TEXT, shipped_at TEXT, delivered_at TEXT)")
            conn.execute("INSERT INTO orders (order_id, user_id, status, paid_amount_cents) "
                         "VALUES (?, ?, ?, ?)",
                         ("ORD-1001", "USER-001", "paid", 9900))
            conn.commit()
        finally:
            conn.close()
        patcher = patch("app.storage.database.DB_PATH", db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_clarification_then_query_keeps_history(self):
        messages = create_messages(build_system_prompt(ORDER_PROMPT, POLICY_PROMPT))
        responses = [reply("请提供订单号"), reply(arguments='{"order_id":"ORD-1001"}'),
                     reply("订单已支付，实付99元")]
        with patch("app.harness.runtime.request_model", side_effect=responses) as model:
            self.assertEqual(run_agent("帮我查订单", "USER-001", messages, None), "请提供订单号")
            self.assertEqual(run_agent("ORD-1001", "USER-001", messages, None), "订单已支付，实付99元")
        self.assertEqual(model.call_count, 3)
        self.assertEqual([m["role"] for m in messages],
                         ["system", "user", "assistant", "user", "assistant", "tool", "assistant"])
        result = json.loads(messages[-2]["content"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["order"]["paid_amount_cents"], 9900)
        self.assertEqual(messages[-2]["tool_call_id"], "call_test")

    def test_other_user_cannot_read_order(self):
        call = reply(arguments='{"order_id":"ORD-1001"}').tool_calls[0]
        self.assertEqual(execute_tool(call, "USER-002"),
                         {"ok": False, "error": "ORDER_NOT_FOUND"})

    def test_invalid_calls_are_rejected(self):
        cases = [("get_order", "not json", "INVALID_ARGUMENTS"),
                 ("get_order", '{"order_id":"ORD-1001","user_id":"USER-001"}', "INVALID_ARGUMENTS"),
                 ("get_order", '{"order_id":42}', "INVALID_ARGUMENTS"),
                 ("delete_order", '{}', "UNKNOWN_TOOL")]
        for name, arguments, error in cases:
            with self.subTest(name=name, arguments=arguments):
                call = reply(arguments=arguments, name=name).tool_calls[0]
                self.assertEqual(execute_tool(call, "USER-001")["error"], error)

    def test_budget_stops_loop_and_sessions_are_separate(self):
        messages = create_messages(build_system_prompt(ORDER_PROMPT, POLICY_PROMPT))
        with patch("app.harness.runtime.request_model",
                   return_value=reply(arguments='{"order_id":"ORD-1001"}')) as model:
            answer = run_agent("查订单", "USER-001", messages, None, max_steps=2)
        self.assertEqual(model.call_count, 2)
        self.assertIn("步数上限", answer)
        self.assertEqual(messages[-1], {"role": "assistant", "content": answer})
        self.assertEqual(len(create_messages(build_system_prompt(ORDER_PROMPT, POLICY_PROMPT))), 1)


if __name__ == "__main__":
    unittest.main()
