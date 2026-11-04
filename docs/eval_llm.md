# 真实 LLM 冒烟评测

这是一条独立的真实模型冒烟路径，不是模型能力基准。

它回答的问题是：真实模型能不能自己选对工具、进入确认流程，并最终让数据库达到期望状态。
它不替代现有的三条脚本化用例；脚本化用例继续负责确定性回归。

## 运行

先确认项目根目录 `.env` 中有：

```dotenv
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

运行：

```bash
conda run --no-capture-output -n retail-agent python -m eval.run_eval --llm --case llm_cancel_paid_order
```

`--case` 可以省略，默认就是 `llm_cancel_paid_order`：

```bash
python -m eval.run_eval --llm
```

指定输出目录：

```bash
python -m eval.run_eval --llm --case llm_cancel_paid_order --output reports/runs/llm-smoke
```

## 执行流程

```text
真实模型收到“我要取消 ORD-EVAL-4，原因是不想要了”
  → 模型自己决定是否调用 cancel_order
  → 如果生成待确认操作，再发送“确认取消”
  → 模型自己识别确认意图
  → 服务端执行后由 Oracle 检查数据库
```

与脚本化用例的区别：

- 用例不提供 `tool_call`，否则等于把答案喂给模型；
- 用例不提供确认意图，模型需要自己识别“确认取消”；
- 模型可能追问、拒绝调用工具或选择其他操作，因此可能得到 `FAIL`。

业务时钟固定为 `2026-11-03T10:00:00+00:00`，与真实运行时间无关。

## 输出

通过示例：

```text
PASS llm_cancel_paid_order
  model: <LLM_MODEL>
  [OK] order.status: 'canceled' (expected 'canceled')
  [OK] refund.count: 1 (expected 1)
  [OK] confirmation.status: 'consumed' (expected 'consumed')
  [OK] operation_logs.count: 1 (expected 1)
result: reports/runs/<timestamp>/llm_cancel_paid_order/result.json
trace:  reports/runs/<timestamp>/llm_cancel_paid_order/trace.jsonl
```

失败示例：

```text
FAIL llm_cancel_paid_order
  model: <LLM_MODEL>
  [FAIL] order.status: 'paid' (expected 'canceled')
  ...
```

失败不一定是程序错误，也可能是模型追问了订单号、没有调用工具，或没有识别确认意图。
实际模型名取自 `.env` 的 `LLM_MODEL`，先看 `trace.jsonl` 中的模型和工具事件。

产物：

```text
reports/runs/<timestamp>/llm_cancel_paid_order/
├── result.json   # 模型回复、待确认操作、Oracle 检查项和耗时
└── trace.jsonl   # 模型、工具和确认事件
```

`reports/runs/` 已在 `.gitignore` 中。

## 用例格式

文件：`eval/llm_cases.jsonl`

```json
{
  "case_id": "llm_cancel_paid_order",
  "user_id": "USER-001",
  "order": {
    "order_id": "ORD-EVAL-4",
    "status": "paid",
    "paid_amount_cents": 19900,
    "paid_minutes_ago": 60
  },
  "user_messages": [
    "我要取消 ORD-EVAL-4，原因是不想要了"
  ],
  "confirmation_message": "确认取消",
  "expected": {
    "outcome": "canceled",
    "order_status": "canceled",
    "refund_count": 1,
    "refund_kind": "cancellation",
    "refund_amount_cents": 19900,
    "refund_status": "completed",
    "confirmation_count": 1,
    "confirmation_status": "consumed",
    "confirmation_result_status": "canceled",
    "operation_logs": 1
  }
}
```

用例只描述用户会说什么和数据库最终应该是什么，不描述模型应该调用什么工具。

## 离线测试

默认测试不访问真实网络：

```bash
conda run -n retail-agent python -m pytest tests/test_llm_eval.py -q
```

结果：`2 passed`。

测试使用假客户端验证：

- 第一次模型响应是 `cancel_order` 工具调用；
- 运行时生成待确认操作；
- 第二次发送“确认取消”；
- 假客户端返回 `report_confirmation_intent(approve)`；
- Oracle 检查取消结果；
- `--llm` CLI 能创建客户端、写 `result.json` 和 `trace.jsonl`。

这些测试只验证管道，不代表真实模型能力。

## 限制

- 单次运行，不使用 `--runs N`，不计算成功率。
- 不支持 `--llm --all`。
- 没有完整的 User Simulator；模型追问时不会自动补充信息。
- 没有 A/B/C Baseline、故障注入和指标汇总。
- 真实调用有网络延迟和 API 成本，结果会受模型版本和随机性影响。
- 单次 PASS 不能证明模型稳定，单次 FAIL 也不能直接证明系统有 bug。

下一步才是多次运行、成功率和违规率统计，然后再做 40 条用例和 A/B/C。