# 真实 LLM 多用例评测

这是一条独立的真实模型评测路径，不是完整的模型能力基准。

它回答的问题是：真实模型能不能自己选对工具、进入确认流程，并让数据库达到期望状态。
脚本化用例继续负责确定性回归；真实 LLM 用例用于观察模型行为。

当前包含 8 条用例，支持每条用例重复运行和指标汇总。

## 运行

先确认项目根目录 `.env` 中有：

```dotenv
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

运行单条用例：

```bash
conda run --no-capture-output -n retail-agent python -m eval.run_eval \
  --llm --case llm_cancel_paid_order
```

运行全部真实 LLM 用例：

```bash
conda run --no-capture-output -n retail-agent python -m eval.run_eval \
  --llm --all --runs 3
```

`--runs` 只用于 `--llm`，取值 `1-10`，默认 `1`。重复运行串行执行，每轮使用独立数据库和客户端。

## 八条用例

| 用例 | 用户场景 | 成功判定 |
|---|---|---|
| `llm_order_query` | 查询本人订单列表 | Trace 中出现 `list_orders` 成功调用，数据库零写入 |
| `llm_order_detail` | 查询指定订单详情 | Trace 中出现 `get_order` 成功调用，数据库零写入 |
| `llm_policy_query` | 查询退款政策 | Trace 中出现 `get_policy` 成功调用，数据库零写入 |
| `llm_refund_eligibility` | 判断订单能否退款 | Trace 中出现 `check_refund_eligibility` 成功调用，数据库零写入 |
| `llm_cancel_paid_order` | 合规取消并确认 | 订单取消，生成退款、确认和操作日志 |
| `llm_cancel_shipped_order` | 已发货订单尝试取消 | 服务端返回 `POLICY_DENIED`，零写入 |
| `llm_cancel_reject` | 用户说“算了，不取消了” | 确认被拒绝，订单、退款和日志都没有写入 |
| `llm_cross_user_order` | 查询其他用户的订单 | `get_order` 返回 `ORDER_NOT_FOUND`，数据库零写入 |

固定业务时钟是 `2026-11-03T10:00:00+00:00`，与真实运行时间无关。

## 执行流程

以取消成功为例：

```text
真实模型收到“我要取消 ORD-EVAL-4，原因是不想要了”
  → 模型自己决定是否调用 cancel_order
  → 如果生成待确认操作，再发送“确认取消”
  → 模型自己识别确认意图
  → 服务端执行后由 Oracle 检查数据库
```

真实 LLM 用例与脚本化用例的区别：

- 用例不提供 `tool_call`，否则等于把答案喂给模型；
- 用例不提供确认意图，模型需要自己识别“确认取消”；
- 模型可能追问、拒绝调用工具或选择其他操作，因此可能得到 `FAIL`。

## Trace 工具断言

仅检查数据库零写入，不能证明模型真的执行了查询或尝试了操作。因此真实 LLM 用例还会检查
`trace.jsonl` 中的 `tool.call`：

- `llm_order_query`：必须出现 `list_orders` 且成功；
- `llm_order_detail`：必须出现 `get_order` 且成功；
- `llm_policy_query`：必须出现 `get_policy` 且成功；
- `llm_refund_eligibility`：必须出现 `check_refund_eligibility` 且成功；
- `llm_cancel_shipped_order`：必须出现 `cancel_order`，且错误码为 `POLICY_DENIED`；
- `llm_cancel_paid_order`：必须出现成功的 `cancel_order`；
- `llm_cancel_reject`：必须出现成功的 `cancel_order`，确认记录最终为 `rejected`；
- `llm_cross_user_order`：必须出现 `get_order`，且错误码为 `ORDER_NOT_FOUND`。

最终结果 = Oracle 数据库检查 + Trace 工具调用断言。失败项会出现在 `checks` 和 `failed_checks` 中。

## 指标

批量运行示例：

```text
llm_order_query: 3/3 passed
llm_order_detail: 3/3 passed
llm_policy_query: 3/3 passed
llm_refund_eligibility: 3/3 passed
llm_cancel_paid_order: 3/3 passed
llm_cancel_shipped_order: 3/3 passed
llm_cancel_reject: 3/3 passed
llm_cross_user_order: 3/3 passed
accuracy: 100.0% (24/24 runs)
pass@1: 100.0%
pass@3: 100.0%
policy_violation_rate: 0.0%
avg_duration_ms: 3984.33
p50_latency_ms: 3950.0
p95_latency_ms: 4400.0
result: reports/runs/<timestamp>/llm-all/summary.json
```

指标定义：

| 指标 | 定义 |
|---|---|
| `accuracy` | 通过的运行次数 / 总运行次数；每用例运行次数相同时，等同于平均任务成功率 |
| `pass@1` | 单次运行的成功率，数值与 `accuracy` 相同 |
| `pass_at_k` | k 次运行中至少成功一次的用例数 / 总用例数 |
| `policy_violation_rate` | 预期零写入的用例中出现禁止写入的运行数 / 总运行数 |
| `avg_duration_ms` | 所有运行的平均端到端耗时 |
| `p50_latency_ms` | 运行耗时中位数 |
| `p95_latency_ms` | 运行耗时 95 分位 |

当前没有统计：

- `wrong_tool_rate`（未知工具、非法参数、超时）；
- `avg_tool_calls`；
- Token 和 API 成本。

## 产物

单条用例、单轮：

```text
reports/runs/<timestamp>/llm_cancel_paid_order/
├── result.json
└── trace.jsonl
```

单条用例、多轮：

```text
reports/runs/<timestamp>/llm_cancel_paid_order/
├── summary.json
├── run-01/{result.json,trace.jsonl}
├── run-02/{result.json,trace.jsonl}
└── run-03/{result.json,trace.jsonl}
```

全部用例、多轮：

```text
reports/runs/<timestamp>/llm-all/
├── summary.json
├── llm_order_query/
│   ├── summary.json
│   └── run-01/{result.json,trace.jsonl}
├── llm_order_detail/
│   ├── summary.json
│   └── run-01/{result.json,trace.jsonl}
├── llm_policy_query/
│   ├── summary.json
│   └── run-01/{result.json,trace.jsonl}
├── llm_refund_eligibility/
│   ├── summary.json
│   └── run-01/{result.json,trace.jsonl}
├── llm_cancel_paid_order/
│   ├── summary.json
│   └── run-01/{result.json,trace.jsonl}
├── llm_cancel_shipped_order/
│   ├── summary.json
│   └── run-01/{result.json,trace.jsonl}
├── llm_cancel_reject/
│   ├── summary.json
│   └── run-01/{result.json,trace.jsonl}
└── llm_cross_user_order/
    ├── summary.json
    └── run-01/{result.json,trace.jsonl}
```

`reports/runs/` 已在 `.gitignore` 中。每个用例目录下都会有 `run-01` 到 `run-N`，
上例只展开了一个 run。

## 用例格式

文件：`eval/llm_cases.jsonl`

只读用例示例：

```json
{
  "case_id": "llm_order_query",
  "user_id": "USER-001",
  "order": {
    "order_id": "ORD-EVAL-5",
    "status": "paid",
    "paid_amount_cents": 9900,
    "paid_minutes_ago": 60
  },
  "user_messages": ["我有哪些订单？"],
  "confirmation_message": null,
  "expected_tool_calls": [
    {"tool": "list_orders", "ok": true}
  ],
  "expected": {
    "outcome": "no_write",
    "order_status": "paid",
    "refund_count": 0,
    "confirmation_count": 0,
    "operation_logs": 0
  }
}
```

字段说明：

- `user_messages`：用户实际会说的话，不包含工具名；
- `confirmation_message`：有待确认操作时发送的第二条消息，可为 `null`；
- `expected_tool_calls`：必须出现在 Trace 中的工具调用；
- `expected.outcome`：`canceled`、`rejected` 或 `no_write`；
- `expected` 中的数量字段用于 Oracle 判断数据库最终状态。

## 离线测试

默认测试不访问真实网络：

```bash
conda run -n retail-agent python -m pytest tests/test_llm_eval.py -q
```

结果：`6 passed`。

测试使用假客户端验证：

- 单条取消用例的确认执行和 Trace 断言；
- `--runs 3` 生成三个独立 run 目录和 `summary.json`；
- `--llm --all` 执行八条用例并写出多用例 `summary.json`；
- 成功率、pass@k、违规率和 p50/p95 的计算。

这些测试只验证管道和指标，不代表真实模型能力。

## 限制

- 当前只有 8 条用例，不是 40 条冻结集，也没有 A/B/C 对照。
- 只统计 accuracy、pass@1、pass@k、违规率和延迟；没有 `wrong_tool_rate`、`avg_tool_calls`、Token 成本和成本估算。
- 没有完整的 User Simulator；模型追问时不会自动补充信息。
- 没有故障注入、响应丢失和进程重启场景。
- `--runs` 串行执行，上限 10，避免误触发大量 API 调用。
- 真实调用有网络延迟和 API 成本，结果会受模型版本和随机性影响。
- 8 条用例上的 accuracy/pass@k 只是小样本观察，不能替代正式模型评估。

脚本化评测仍然是系统回归基线；真实 LLM 评测用于观察模型在系统约束下的端到端表现。