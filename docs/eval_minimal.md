# 最小评测闭环（取消、退款申请、撤回与响应丢失）

本轮实现 18 条可运行用例，不是完整的 40 条用例和 A/B/C 对照。

取消（6 条）：

- `cancel_paid_order`：显式批准，合规取消成功；
- `cancel_unpaid_order`：未付款订单取消成功，不生成退款流水；
- `cancel_shipped_order`：已发货订单不能取消，服务端拒绝且数据库零写入；
- `cancel_paid_order_chat_confirm`：两轮对话，用户说“确认取消”，由意图识别批准并执行；
- `cancel_paid_order_reject`：用户显式拒绝，订单不变，确认记录为 `rejected`；
- `cancel_over_24h_window`：付款超过 24 小时，超窗口拒绝且零写入。

退款申请（5 条）：

- `refund_shipped_order_chat_confirm`：确认后生成 `pending` 售后申请，订单状态不变；
- `refund_explicit_confirm`：同一路径改用显式批准；
- `refund_shipped_order_reject`：用户明确拒绝，不创建退款申请；
- `refund_paid_order_denied`：未发货订单不能申请退款；
- `refund_out_of_window`：发货超过 7 天，超窗口拒绝。

撤回（3 条）：

- `withdraw_pending_refund`：预置 `pending` 申请，确认后变为 `withdrawn`，订单不变；
- `withdraw_without_refund`：没有可撤回申请，服务端拒绝且零写入；
- `withdraw_already_withdrawn`：申请已撤回，不能重复撤回。

提交后响应丢失（4 条，详见 [故障注入与恢复](fault_injection.md)）：

- `fault_cancel_response_lost`：取消已提交但响应丢失，恢复查询为 `executed`，重放不新增业务行；
- `fault_refund_response_lost`：退款申请已提交但响应丢失，申请仍为 `pending`，订单不变；
- `fault_withdraw_response_lost`：撤回已提交但响应丢失，申请 `withdrawn`，金额不变；
- `fault_chat_cancel_response_lost`：自然语言确认下整轮回复丢失，恢复与重放结论相同。

评测只根据 SQLite 最终状态判断成功，不相信 Agent 的回复。

## 运行

```bash
conda run -n retail-agent python -m eval.run_eval --all
conda run -n retail-agent python -m eval.run_eval --case withdraw_pending_refund
conda run -n retail-agent python -m eval.run_eval --case cancel_unpaid_order
```

等价的安装后入口：

```bash
retail-eval --all
retail-eval --case withdraw_pending_refund
retail-eval --case cancel_unpaid_order
```

指定输出目录：

```bash
python -m eval.run_eval --case cancel_paid_order_chat_confirm --output reports/runs/demo
```

批量运行全部用例：

```bash
conda run -n retail-agent python -m eval.run_eval --all
retail-eval --all
```

批量运行会为每条用例创建子目录，并生成 `summary.json`。`--case` 和 `--all` 互斥；
不传参数时仍然只运行 `cancel_paid_order`。

## 预期输出

显式确认成功：

```text
PASS cancel_paid_order
  [OK] order.status: 'canceled' (expected 'canceled')
  [OK] refund.cancellation_count: 1 (expected 1)
  [OK] refund.after_sales_count: 0 (expected 0)
  [OK] confirmation.status: 'consumed' (expected 'consumed')
  [OK] operation_logs.count: 1 (expected 1)
```

服务端拒绝：

```text
PASS cancel_shipped_order
  [OK] order.status: 'shipped' (expected 'shipped')
  [OK] refund.cancellation_count: 0 (expected 0)
  [OK] refund.after_sales_count: 0 (expected 0)
  [OK] confirmation.count: 0 (expected 0)
  [OK] operation_logs.count: 0 (expected 0)
```

自然语言确认：

```text
PASS cancel_paid_order_chat_confirm
  [OK] order.status: 'canceled' (expected 'canceled')
  [OK] refund.cancellation_count: 1 (expected 1)
  [OK] refund.after_sales_count: 0 (expected 0)
  [OK] confirmation.status: 'consumed' (expected 'consumed')
  [OK] operation_logs.count: 1 (expected 1)
```

退款申请提交成功：

```text
PASS refund_shipped_order_chat_confirm
  [OK] order.status: 'shipped' (expected 'shipped')
  [OK] refund.cancellation_count: 0 (expected 0)
  [OK] refund.after_sales_count: 1 (expected 1)
  [OK] refund.kind: 'after_sales' (expected 'after_sales')
  [OK] refund.status: 'pending' (expected 'pending')
  [OK] confirmation.status: 'consumed' (expected 'consumed')
  [OK] confirmation.result_status: 'submitted' (expected 'submitted')
  [OK] operation_logs.count: 0 (expected 0)
```

用户拒绝退款申请：

```text
PASS refund_shipped_order_reject
  [OK] order.status: 'shipped' (expected 'shipped')
  [OK] refund.cancellation_count: 0 (expected 0)
  [OK] refund.after_sales_count: 0 (expected 0)
  [OK] confirmation.count: 1 (expected 1)
  [OK] confirmation.status: 'rejected' (expected 'rejected')
  [OK] operation_logs.count: 0 (expected 0)
```

批量运行：

```text
PASS cancel_paid_order
PASS cancel_shipped_order
PASS cancel_paid_order_chat_confirm
PASS refund_shipped_order_chat_confirm
PASS refund_shipped_order_reject
PASS cancel_unpaid_order
PASS cancel_paid_order_reject
PASS cancel_over_24h_window
PASS refund_paid_order_denied
PASS refund_out_of_window
PASS refund_explicit_confirm
PASS withdraw_pending_refund
PASS withdraw_without_refund
PASS withdraw_already_withdrawn
PASS fault_cancel_response_lost
PASS fault_refund_response_lost
PASS fault_withdraw_response_lost
PASS fault_chat_cancel_response_lost
summary: 18/18 passed
result: reports/runs/<timestamp>/summary.json
```

进程退出码：

- `0`：Oracle 全部通过；
- `1`：Oracle 有失败项或执行异常；
- `2`：用例不存在或用例文件不合法。

## 用例清单

| 用例 | 初始订单 | 确认方式 | 期望结果 |
|---|---|---|---|
| `cancel_paid_order` | 已付款 259 元，1 小时前付款 | 显式 `approve` | 订单取消，生成退款、确认和日志 |
| `cancel_unpaid_order` | 未付款，实付 0 元 | 显式 `approve` | 订单取消，不生成退款流水，写 1 条日志 |
| `cancel_shipped_order` | 已发货 159 元，1 小时前发货 | 无确认，服务端预检拒绝 | `POLICY_DENIED`，零写入 |
| `cancel_paid_order_chat_confirm` | 已付款 129 元，30 分钟前付款 | 第二轮“确认取消” | 意图识别为 `approve` 后执行 |
| `cancel_paid_order_reject` | 已付款 99 元，1 小时前付款 | 显式 `reject` | 订单不变，确认记录 `rejected` |
| `cancel_over_24h_window` | 已付款 99 元，25 小时前付款 | 无确认，服务端预检拒绝 | `WINDOW_EXPIRED`，零写入 |
| `refund_shipped_order_chat_confirm` | 已发货 89 元，1 小时前发货 | 第二轮“确认提交” | 生成 `pending` 售后申请，订单仍为 `shipped` |
| `refund_explicit_confirm` | 已发货 69 元，1 小时前发货 | 显式 `approve` | 生成 `pending` 售后申请 |
| `refund_shipped_order_reject` | 已发货 89 元，1 小时前发货 | 第二轮“算了，不申请了” | 确认记录为 `rejected`，不生成退款申请 |
| `refund_paid_order_denied` | 已付款 89 元，未发货 | 无确认，服务端预检拒绝 | `STATUS_NOT_SUPPORTED`，零写入 |
| `refund_out_of_window` | 已发货 89 元，约 7.6 天前发货 | 无确认，服务端预检拒绝 | `WINDOW_EXPIRED`，零写入 |
| `withdraw_pending_refund` | 已发货 89 元，预置 `pending` 申请 | 第二轮“确认撤回” | 申请变为 `withdrawn`，订单不变，确认记录 `consumed` |
| `withdraw_without_refund` | 已发货 89 元，无售后申请 | 无确认，服务端预检拒绝 | `NO_ACTIVE_REFUND`，零写入 |
| `withdraw_already_withdrawn` | 已发货 89 元，预置 `withdrawn` 申请 | 无确认，服务端预检拒绝 | `NO_ACTIVE_REFUND`，申请原样保留 |
| `fault_cancel_response_lost` | 已付款 159 元，1 小时前付款 | 显式 `approve`，提交后丢弃响应 | 订单取消、生成退款；恢复为 `executed`，重放不新增行 |
| `fault_refund_response_lost` | 已发货 89 元，1 小时前发货 | 显式 `approve`，提交后丢弃响应 | 申请 `pending`，订单仍 `shipped`；重放不新增行 |
| `fault_withdraw_response_lost` | 已发货 89 元，预置 `pending` 申请 | 显式 `approve`，提交后丢弃响应 | 申请 `withdrawn`，金额不变；重放不新增行 |
| `fault_chat_cancel_response_lost` | 已付款 129 元，30 分钟前付款 | 第二轮“确认取消”，整轮回复丢弃 | 订单取消、生成退款；恢复为 `executed`，重放不新增行 |

固定业务时钟是 `2026-11-03T10:00:00+00:00`，结果可重复。

## 执行流程

显式确认：

```text
send_message(用户请求)
  → 脚本化模型返回 cancel_order
  → 生成待确认操作
  → decide_confirmation(approve)
  → Oracle
```

服务端拒绝：

```text
send_message(用户请求)
  → 脚本化模型仍返回 cancel_order
  → 业务预检返回 POLICY_DENIED
  → 脚本化模型返回解释文本
  → 没有待确认操作
  → Oracle 检查零写入
```

自然语言确认：

```text
send_message("我要取消 ORD-EVAL-3")
  → 脚本化模型返回 cancel_order
  → 生成待确认操作
send_message("确认取消")
  → 运行时调用意图识别模型
  → 脚本化模型返回 report_confirmation_intent(approve)
  → 服务端批准并执行
  → Oracle 检查取消结果
```

取消的自然语言确认需要两次脚本化响应：第一次是 `cancel_order` 工具调用，第二次是
`report_confirmation_intent` 工具调用。

退款申请提交：

```text
send_message("ORD-EVAL-12 商品破损，我要申请退款")
  → 脚本化模型返回 prepare_refund_confirmation
  → 生成待确认操作
send_message("确认提交")
  → 运行时调用意图识别模型
  → 脚本化模型返回 report_confirmation_intent(approve)
  → 服务端复查退款资格和金额
  → 新增 after_sales / pending 申请，确认记录变为 consumed
  → Oracle 检查订单仍为 shipped、没有取消退款和操作日志
```

用户拒绝退款申请：

```text
send_message("ORD-EVAL-13 商品破损，我要申请退款")
  → 脚本化模型返回 prepare_refund_confirmation
  → 生成待确认操作
send_message("算了，不申请了")
  → 运行时调用意图识别模型
  → 脚本化模型返回 report_confirmation_intent(reject)
  → 服务端将确认记录标记为 rejected
  → 不创建退款申请
  → Oracle 检查订单仍为 shipped、退款和操作日志为 0
```

## 用例字段

`confirmation.mode` 支持：

- `explicit`：直接调用 `decide_confirmation`，需要 `decision` 为 `approve` 或 `reject`；
- `chat`：发送第二条用户消息，需要 `message`、`intent` 和 `evidence`；
- `none`：不期待确认操作，只用于服务端预检拒绝的用例。

`tool_call.name` 支持 `cancel_order`、`prepare_refund_confirmation` 和 `withdraw_refund`；
`withdraw_refund` 只接受 `order_id`，传入 `reason` 会被 schema 拒绝。

`existing_refund` 可选，仅用于撤回用例，表示评测开始前数据库中已存在的售后申请：

```json
{"status": "pending", "amount_cents": 8900, "reason": "商品破损"}
```

`status` 只支持 `pending`、`withdrawn`、`completed`；`amount_cents` 必须等于订单实付金额，
`reason` 默认为「商品破损」。该申请由 `eval/fixtures.py` 在订单之后写入。
在非撤回用例上使用该字段会被 schema 拒绝。

自然语言确认示例：

```json
{
  "case_id": "cancel_paid_order_chat_confirm",
  "order": {
    "order_id": "ORD-EVAL-3",
    "status": "paid",
    "paid_amount_cents": 12900,
    "paid_minutes_ago": 30
  },
  "tool_call": {
    "name": "cancel_order",
    "arguments": {"order_id": "ORD-EVAL-3", "reason": "不想要了"}
  },
  "confirmation": {
    "mode": "chat",
    "message": "确认取消",
    "intent": "approve",
    "evidence": "确认取消"
  },
  "expected": {
    "outcome": "canceled",
    "order_status": "canceled",
    "refund_count": 1,
    "refund_kind": "cancellation",
    "refund_amount_cents": 12900,
    "refund_status": "completed",
    "confirmation_count": 1,
    "confirmation_status": "consumed",
    "confirmation_result_status": "canceled",
    "operation_logs": 1
  }
}
```

`expected.outcome` 支持：

- `canceled`：成功取消。已付款订单必须恰好一条取消退款、一条确认、一条操作日志；未付款订单不生成退款流水，但仍需一条确认和一条操作日志；
- `rejected`：服务端预检拒绝时，退款、确认和操作日志数量都必须为 0（预置申请除外，见下）；用户明确拒绝时，必须恰好有一条 `rejected` 确认记录，退款和操作日志为 0；
- `refund_submitted`：成功提交退款申请，订单状态不变，必须恰好一条 `after_sales` 的 `pending` 申请和一条 `consumed` 确认，操作日志为 0；
- `refund_withdrawn`：成功撤回申请，订单状态不变，申请变为 `withdrawn`，金额不变，确认记录为 `consumed` 且工具为 `cancel_refund`，操作日志为 0。

`refund_kind` 决定 Oracle 检查哪一类退款：`cancellation` 对应取消退款，
`after_sales` 对应售后申请；另一类必须为 0。

## 文件与职责

| 文件 | 职责 |
|---|---|
| `eval/dev_cases.jsonl` | 18 条用例 |
| `eval/case_schema.py` | 加载并校验订单、工具调用、预置申请、确认方式和期望结果 |
| `eval/fixtures.py` | 隔离数据库、固定时钟、写入付款/发货时间和预置申请 |
| `eval/harness.py` | 脚本化模型客户端，执行显式确认、自然语言确认或拒绝路径 |
| `eval/oracle.py` | 独立读取 SQLite，按期望数量检查业务状态 |
| `eval/reporter.py` | 汇总逐条结果，生成 `summary.json` |
| `eval/run_eval.py` | CLI 入口，支持单条 `--case` 和批量 `--all` |
| `tests/test_eval_harness.py` | 用例执行、Oracle 负向判定、schema 拒绝路径、批量汇总和 CLI 产物 |

## Oracle 检查项

所有用例都会检查：

- 订单最终状态符合期望；
- 订单实付金额没有被修改；
- `cancellation` 和 `after_sales` 两类退款数量分别符合期望；
- 确认记录数量符合期望；
- 操作日志数量符合期望；
- 其他订单没有被取消。

成功用例额外检查：

- 取消：退款类型为 `cancellation`，金额和状态正确；确认记录状态为 `consumed`，保存结果为 `canceled`；操作日志记录从 `paid` 到 `canceled`。未付款取消不检查退款，只检查日志的金额为 0。
- 退款申请：退款类型为 `after_sales`，金额等于实付金额，状态为 `pending`；确认记录状态为 `consumed`，保存结果为 `submitted`；订单状态保持不变，操作日志为 0。
- 撤回：申请类型为 `after_sales`，金额不变，状态为 `withdrawn`；确认记录状态为 `consumed`，保存结果为 `withdrawn`，工具为 `cancel_refund`；订单状态不变，操作日志为 0。

拒绝用例额外要求：

- 订单仍为初始状态；
- 退款和操作日志数量为 0；
- 服务端预检拒绝时确认数量为 0；用户明确拒绝时确认数量为 1 且状态为 `rejected`。

Oracle 不调用 Agent、不调用业务 Service、不读 Agent 回复。

## 产物

单条运行：

```text
reports/runs/<timestamp>/
├── result.json   # 用例、检查项、通过状态和耗时
└── trace.jsonl   # 本轮 Agent 的模型、工具和确认事件
```

批量运行：

```text
reports/runs/<timestamp>/
├── summary.json
├── cancel_paid_order/
│   ├── result.json
│   └── trace.jsonl
├── cancel_shipped_order/
│   ├── result.json
│   └── trace.jsonl
├── ...
└── withdraw_already_withdrawn/
    ├── result.json
    └── trace.jsonl
```

`summary.json` 包含：

- `total`、`passed`、`failed` 和总耗时；
- 每条用例的 `passed`、`outcome`、确认方式、耗时和失败检查项；
- 失败用例的 `error`。

`reports/runs/` 已在 `.gitignore` 中。

## 这一轮明确未实现

- 40 条冻结用例；
- A/B/C Baseline；
- 故障注入、重试、响应丢失和进程重启场景；
- 失败案例报告；
- LangGraph Checkpointer 集成。

真实 LLM 多用例评测和重复运行已单独实现，见 [真实 LLM 多用例评测](eval_llm.md)；
它不是本文件描述的确定性回归基线。

当前脚本化模型只验证 Harness 和 Oracle 本身，不能用来比较模型好坏。

## 测试

```bash
conda run -n retail-agent python -m pytest tests/test_eval_harness.py -q
```

预期：`16 passed`。

负向测试会故意制造错误状态，确认 Oracle 不会永远返回通过：

- 成功用例没有执行取消时，订单仍为 `paid`、退款数量为 0，Oracle 必须失败；
- 拒绝用例被人为写入一条取消退款时，`refund.cancellation_count` 必须失败；
- 退款申请用例没有真正提交申请时，`refund.after_sales_count` 和 `confirmation.count` 必须失败；
- 用户拒绝退款后被写入一条售后申请时，`refund.after_sales_count` 必须失败。

批量测试还会确认：

- `--all` 执行全部用例；
- 每条用例都有自己的 `result.json` 和 `trace.jsonl`；
- `summary.json` 的 `total/passed/failed` 正确。