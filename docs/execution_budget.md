# 执行预算

`max_steps` 限制的是「模型循环几次」，管不住「一次循环里调了多少个工具」。
一个模型完全可以在第一次响应里返回 8 个工具调用，把取消、退款、查询一起发出去。
单次调用也可能因为数据库锁或慢查询拖很久。

执行预算补上这两个缺口：单轮工具调用总数与总耗时。

## 两个上限

| 上限 | 默认值 | 触发后 |
|---|---|---|
| 工具调用总数 | 8 | 超出的工具调用不执行，补一条 `BUDGET_EXCEEDED` 结果 |
| 总耗时 | 30 秒 | 不再发起新的模型调用，直接返回超时说明 |

`max_steps` 仍然生效，三者是并列的三道闸。

## 行为

超限时**不抛异常**，而是走一条确定性路径：

1. 已执行的工具保持执行结果，不回滚；
2. 超出配额的每个 `tool_call` 都补一条 `{"ok": false, "error": "BUDGET_EXCEEDED"}`；
3. 返回固定说明，明确告知「已执行的变更不会回滚，请先查询状态」；
4. 写一条 `budget.exceeded` Trace 事件，含 `kind`、`step`、已用量与上限。

## 为什么必须补结果

工具调用是成对的消息：`assistant(tool_calls)` 之后必须跟同样数量的 `tool` 消息。
只执行前几个、剩下的直接丢弃，会让消息序列不合法，**下一轮请求直接 400**，
整段会话报废。

这一点和上下文裁剪的边界对齐是同一个约束：消息序列的完整性优先于「少做一点」。

## 配置

常量位于 `app/harness/budget.py`：

| 常量 | 默认值 |
|---|---|
| `MAX_TOOL_CALLS_PER_RUN` | 8 |
| `MAX_RUN_DURATION_SECONDS` | 30.0 |

调用方也可以传入自定义实例，测试用的就是注入时钟：

```python
from app.harness.budget import ExecutionBudget

budget = ExecutionBudget(max_tool_calls=3, max_seconds=5, clock=fake_clock)
run_agent(message, user_id, messages, client, budget=budget)
```

## 运行与验收

```powershell
conda run -n retail-agent python -m pytest tests/test_execution_budget.py -q
```

覆盖的检查：

- 一次返回 10 个工具调用、限额 3 时，恰好 3 个执行、7 个返回 `BUDGET_EXCEEDED`；
- 每个 `tool_call` 都有对应的 `tool` 消息（配对完整）；
- 额度足够时全部执行；
- 注入时钟使耗时超限后，**模型完全不会被调用**；
- `budget.exceeded` 事件字段正确；
- 流式路径行为一致。

## 当前限制

- 耗时只按单调时钟统计整轮，不区分「模型慢」还是「工具慢」，也不做单次工具的超时叠加。
- 超限后不自动回滚已执行的写操作；改为提示用户查询状态，这与「写入只在确认后发生」的既有约定一致。
- 预算按单轮重置，不跨轮累计；跨会话的成本控制不在此范围。
