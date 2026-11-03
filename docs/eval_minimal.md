# 最小评测闭环（取消成功与拒绝路径）

本轮实现的是评测系统的两条可运行链路，不是完整的 40 条用例和 A/B/C 对照：

- `cancel_paid_order`：合规取消成功，检查订单、退款、确认和日志；
- `cancel_shipped_order`：已发货订单不能取消，检查服务端拒绝且数据库零写入。

评测只根据 SQLite 最终状态判断成功，不相信 Agent 的回复。

## 运行

```bash
conda run -n retail-agent python -m eval.run_eval --case cancel_paid_order
conda run -n retail-agent python -m eval.run_eval --case cancel_shipped_order
```

等价的安装后入口：

```bash
retail-eval --case cancel_paid_order
retail-eval --case cancel_shipped_order
```

指定输出目录：

```bash
python -m eval.run_eval --case cancel_shipped_order --output reports/runs/demo
```

## 预期输出

成功路径：

```text
PASS cancel_paid_order
  [OK] order.status: 'canceled' (expected 'canceled')
  [OK] refund.count: 1 (expected 1)
  [OK] refund.status: 'completed' (expected 'completed')
  [OK] confirmation.status: 'consumed' (expected 'consumed')
  [OK] operation_logs.count: 1 (expected 1)
  ...
```

拒绝路径：

```text
PASS cancel_shipped_order
  [OK] order.status: 'shipped' (expected 'shipped')
  [OK] refund.count: 0 (expected 0)
  [OK] confirmation.count: 0 (expected 0)
  [OK] operation_logs.count: 0 (expected 0)
  [OK] other_orders.canceled: 0 (expected 0)
```

进程退出码：

- `0`：Oracle 全部通过；
- `1`：Oracle 有失败项或执行异常；
- `2`：用例不存在或用例文件不合法。

## 两条用例

| 用例 | 初始订单 | 脚本化模型行为 | 期望结果 |
|---|---|---|---|
| `cancel_paid_order` | 已付款 259 元，1 小时前付款，未发货 | 调用 `cancel_order` | 显式批准后订单取消，生成退款、确认和日志 |
| `cancel_shipped_order` | 已发货 159 元，1 小时前发货 | 仍调用 `cancel_order` | 服务端 `POLICY_DENIED`，不生成待确认操作，零写入 |

固定业务时钟是 `2026-11-03T10:00:00+00:00`，结果可重复。

## 执行流程

成功路径：

```text
加载用例
  → 创建临时 SQLite 数据库
  → 写入已付款订单
  → 脚本化模型返回一次 cancel_order 工具调用
  → 运行时生成待确认操作
  → 显式批准
  → Oracle 直接读数据库判定
```

拒绝路径：

```text
加载用例
  → 创建临时 SQLite 数据库
  → 写入已发货订单
  → 脚本化模型仍返回 cancel_order 工具调用
  → 业务预检返回 POLICY_DENIED
  → 运行时没有待确认操作，模型返回解释文本
  → Oracle 检查订单仍为 shipped，且没有任何写入
```

拒绝路径的脚本化模型需要两次响应：第一次是工具调用，第二次是工具被拒后的解释文本。

## 用例字段

核心字段：

```json
{
  "case_id": "cancel_shipped_order",
  "order": {
    "order_id": "ORD-EVAL-2",
    "status": "shipped",
    "paid_amount_cents": 15900,
    "paid_minutes_ago": 120,
    "shipped_minutes_ago": 60
  },
  "tool_call": {
    "name": "cancel_order",
    "arguments": {"order_id": "ORD-EVAL-2", "reason": "不想要了"}
  },
  "expected": {
    "outcome": "rejected",
    "order_status": "shipped",
    "refund_count": 0,
    "confirmation_count": 0,
    "operation_logs": 0
  }
}
```

`expected.outcome` 只支持：

- `canceled`：成功取消，必须恰好一条退款、一条确认、一条操作日志；
- `rejected`：服务端拒绝，退款、确认和操作日志数量都必须为 0。

## 文件与职责

| 文件 | 职责 |
|---|---|
| `eval/dev_cases.jsonl` | 成功与拒绝两条用例 |
| `eval/case_schema.py` | 加载并校验用例字段 |
| `eval/fixtures.py` | 隔离数据库、固定时钟、写入付款/发货时间 |
| `eval/harness.py` | 脚本化模型客户端，执行成功或拒绝路径 |
| `eval/oracle.py` | 独立读取 SQLite，按期望数量检查业务状态 |
| `eval/run_eval.py` | CLI 入口，打印结果并写 `result.json` |
| `tests/test_eval_harness.py` | 正向、拒绝、Oracle 负向判定和 CLI 产物 |

## Oracle 检查项

两条用例都会检查：

- 订单最终状态符合期望；
- 订单实付金额没有被修改；
- 退款数量和售后申请数量符合期望；
- 确认记录数量符合期望；
- 操作日志数量符合期望；
- 其他订单没有被取消。

成功用例额外检查：

- 退款类型为 `cancellation`，金额和状态正确；
- 确认记录状态为 `consumed`，保存结果为 `canceled`；
- 操作日志记录从 `paid` 到 `canceled`。

拒绝用例额外要求：

- 订单仍为 `shipped`；
- 退款、确认和操作日志数量全部为 0。

Oracle 不调用 Agent、不调用业务 Service、不读 Agent 回复。

## 产物

```text
reports/runs/<timestamp>/
├── result.json   # 用例、检查项、通过状态和耗时
└── trace.jsonl   # 本轮 Agent 的模型、工具和确认事件
```

`reports/runs/` 已在 `.gitignore` 中。

## 这一轮明确未实现

- 真实 LLM 调用和模型能力评估；
- 40 条冻结用例和批量运行；
- A/B/C Baseline；
- User Simulator 和自然语言确认；
- 故障注入、重试、响应丢失和进程重启场景；
- `metrics.py`、`reporter.py`、失败案例报告；
- LangGraph Checkpointer 集成。

当前脚本化模型只验证 Harness 和 Oracle 本身，不能用来比较模型好坏。

## 测试

```bash
conda run -n retail-agent python -m pytest tests/test_eval_harness.py -q
```

预期：`6 passed`。

负向测试会故意制造错误状态，确认 Oracle 不会永远返回通过：

- 成功用例没有执行取消时，订单仍为 `paid`、退款数量为 0，Oracle 必须失败；
- 拒绝用例被人为写入一条退款时，`refund.count` 必须失败。