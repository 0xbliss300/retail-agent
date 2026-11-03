# 工具超时与失败处理

本轮给 Agent 的 `model → tool → model` 循环增加统一的工具执行边界。它解决的问题是：
工具卡住、SQLite 锁冲突或代码异常时，整轮对话不应崩溃、卡死，模型也不应继续编造成功结果。

## 用户可见行为

- 查询订单、物流、退款资格时，如果工具超时或执行失败，Agent 会明确说明本次查询未完成，并建议稍后重试。
- 取消订单、准备退款确认时，如果工具超时，Agent 会提示结果未确认，并提醒先查询订单和退款状态、不要重复提交。
- 只读工具遇到临时故障可以自动重试一次；写工具不会自动重试。
- 业务拒绝（例如 `ORDER_NOT_FOUND`、`POLICY_DENIED`）不当作工具故障，仍由模型按业务结果解释。

## 错误码

| 错误码 | 含义 | 是否自动重试 |
|---|---|---|
| `TOOL_TIMEOUT` | 工具在限定时间内没有返回 | 只读工具重试一次 |
| `TOOL_BUSY` | SQLite 返回 locked/busy | 只读工具重试一次 |
| `TOOL_ERROR` | 工具抛出未预期异常 | 不重试 |
| `INVALID_ARGUMENTS` | 参数缺失、类型错误或包含多余字段 | 不重试 |
| `UNKNOWN_TOOL` | 模型调用了未注册的工具名 | 不重试 |

`TOOL_TIMEOUT`、`TOOL_BUSY`、`TOOL_ERROR` 只表示执行边界失败，不代表业务操作已经成功或已经失败。
涉及取消、退款时，必须先查询订单和退款状态。

## 只读与写工具

只读工具允许安全重试：

- `list_orders`
- `get_order`
- `get_logistics`
- `get_policy`
- `check_refund_eligibility`
- `get_refund_status`

写工具不自动重试：

- `cancel_order`
- `prepare_refund_confirmation`

重试策略集中在 `app/harness/recovery.py`：

- 只读工具最多执行两次（一次执行加一次重试）；
- 只对 `TOOL_TIMEOUT` 和 `TOOL_BUSY` 重试；
- 写工具只执行一次。

## 失败时如何回复

单个工具调用失败时，`app/harness/runtime.py` 不再请求模型继续解释，而是直接返回确定性说明：

- `TOOL_TIMEOUT`：本次工具调用超时，结果未确认，请稍后重试；涉及取消或退款时先查询状态，不要重复提交。
- `TOOL_BUSY`：当前数据库繁忙，本次操作未完成，请稍后重试。
- `TOOL_ERROR`：工具执行失败，本次操作未完成，请稍后重试。

多个工具调用中有一个失败时，错误结果仍会作为 tool message 交给模型，由模型结合其他结果解释。
系统提示词也补充了规则：工具返回 error 时不得声称操作成功。

## 实现位置

| 文件 | 作用 |
|---|---|
| `app/harness/tool_executor.py` | 参数校验、工具分派、超时、异常归一化 |
| `app/harness/recovery.py` | 只读/写工具分类和重试决策 |
| `app/harness/runtime.py` | 工具失败时的确定性回复 |
| `app/agent/prompts.py` | 错误码使用规则 |
| `tests/test_tool_recovery.py` | 超时、数据库忙、异常和重试差异 |

超时目前是 `tool_executor.TOOL_TIMEOUT_SECONDS = 5.0` 秒，只读工具重试一次。

## 验证

```bash
conda run -n retail-agent python -m pytest tests/test_tool_recovery.py -q
```

覆盖：

1. 只读工具超时后重试一次，最终返回 `TOOL_TIMEOUT`；
2. 只读工具遇到 `database is locked` 后重试成功；
3. 写工具超时不重试，订单、确认和操作日志都没有新增；
4. 普通异常归一化为 `TOOL_ERROR`，不重试；
5. 单个工具超时时，运行时直接返回固定说明，不再请求模型。

回归：

```bash
conda run -n retail-agent python -m pytest tests/test_runtime.py tests/test_tools.py tests/test_cancellation.py -q
```

## 限制

- Python 线程无法强制终止已经卡住的调用。超时只保证主流程及时返回，后台工作线程可能继续执行。
- 对当前本地 SQLite 查询，这个限制通常可以接受；生产环境应使用子进程隔离、可中断 I/O 或数据库超时。
- 写工具超时后业务状态可能未知，因此固定回复要求用户先查询订单和退款状态，不能直接推断成功或失败。
- 当前还没有把工具耗时和重试写入 Trace；下一步可接入 `app/observability/tracing.py`。