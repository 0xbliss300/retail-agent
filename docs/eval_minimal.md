# 最小评测闭环（单条取消用例）

本轮实现的是评测系统的第一条可运行链路，不是完整的 40 条用例和 A/B/C 对照。

它验证的是：评测框架能否在独立数据库里执行一次取消操作，并且只根据数据库状态判断成功，
而不相信 Agent 的回复。

## 运行

```bash
conda run -n retail-agent python -m eval.run_eval --case cancel_paid_order
```

等价的安装后入口：

```bash
retail-eval --case cancel_paid_order
```

指定输出目录：

```bash
python -m eval.run_eval --case cancel_paid_order --output reports/runs/demo
```

## 预期输出

```text
PASS cancel_paid_order
  [OK] order.status: 'canceled' (expected 'canceled')
  [OK] refund.count: 1 (expected 1)
  [OK] refund.amount_cents: 25900 (expected 25900)
  [OK] confirmation.status: 'consumed' (expected 'consumed')
  [OK] operation_logs.count: 1 (expected 1)
  ...
result: reports/runs/<timestamp>/result.json
trace:  reports/runs/<timestamp>/trace.jsonl
```

进程退出码：

- `0`：Oracle 全部通过；
- `1`：Oracle 有失败项或执行异常；
- `2`：用例不存在或用例文件不合法。

## 执行流程

```text
加载 eval/dev_cases.jsonl 中的 cancel_paid_order
  → 创建临时 SQLite 数据库
  → 写入 ORD-EVAL-1（已付款 259 元，1 小时前付款，未发货）
  → 脚本化模型返回一次 cancel_order 工具调用
  → 运行时生成待确认操作
  → 显式批准
  → Oracle 直接读数据库判定
```

固定业务时钟是 `2026-11-03T10:00:00+00:00`，确保订单在 24 小时取消窗口内，结果可重复。

## 文件与职责

| 文件 | 职责 |
|---|---|
| `eval/dev_cases.jsonl` | 当前唯一用例 `cancel_paid_order` |
| `eval/case_schema.py` | 加载并校验用例字段 |
| `eval/fixtures.py` | 隔离数据库、固定时钟、写入用例订单 |
| `eval/harness.py` | 脚本化模型客户端，驱动 `send_message` 和 `decide_confirmation` |
| `eval/oracle.py` | 独立读取 SQLite，检查订单、退款、确认、日志和禁止副作用 |
| `eval/run_eval.py` | CLI 入口，打印结果并写 `result.json` |
| `tests/test_eval_harness.py` | 正向用例、Oracle 负向判定、CLI 产物 |

## Oracle 检查项

- 订单状态为 `canceled`；
- 订单实付金额没有被修改；
- 恰好一条 `cancellation` 退款流水，金额和状态正确；
- 没有生成 `after_sales` 售后申请；
- 确认记录恰好一条，状态为 `consumed`，保存结果为 `canceled`；
- 操作日志恰好一条，记录从 `paid` 到 `canceled`；
- 其他订单没有被取消。

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
- 40 条冻结用例；
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

预期：`3 passed`。

其中负向测试故意不执行取消，确认 Oracle 会因为订单仍为 `paid`、退款数量为 0 而判定失败。