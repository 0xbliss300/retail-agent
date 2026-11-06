# 统一执行内核

`app/harness/runtime.py` 里曾有两份几乎相同的执行循环：`run_agent` 负责普通对话，
`stream_agent` 负责流式对话。确认拦截、域外闸门、执行预算、上下文裁剪、模型调用、
工具消息配对、确定性回复和 Trace 各写了一遍。新增一条运行时规则要改两处，
漏掉一处就会让 `/chat` 与 `/chat/stream` 对同一句话给出不同结果。

现在两条入口共用 `_execute` 生成器内核，`stream` 参数只决定输出通道。

## 职责划分

| 层 | 职责 |
|---|---|
| `_execute` | 唯一的执行内核：所有闸门、状态变更、消息历史和 Trace |
| `run_agent` | 驱动内核，取生成器 `return` 的最终回复 |
| `stream_agent` | `yield from` 内核，把 token 事件实时透传给 SSE |
| `conversation.py` | 会话打开、历史保存、`agent.start/end`；调用签名未变 |

## 两种输出通道

| | 模型调用 | 模型文本 | 确定性回复 |
|---|---|---|---|
| 非流式 | `request_model` 一次返回 | 直接作为返回值 | 写入历史，不产出事件 |
| 流式 | `stream_model` 逐块 | 增量 token 实时输出 | 写入历史，再输出一条完整 token |

模型直接给出的文本不走 `_emit_answer`：它的 assistant 消息已经写入历史，
流式增量 token 也已经输出，重复输出会让 `done.reply` 变成两遍。
确认、预算超限、单工具失败、步数上限等确定性回复才由 `_emit_answer` 统一收口。

## 保持不变的行为

- `run_agent`、`stream_agent` 的参数、返回值和 SSE 事件结构；
- 工具调用与工具结果严格配对，超预算与等待确认时补齐
  `BUDGET_EXCEEDED` / `AWAITING_CONFIRMATION`；
- 预算超限时已执行的写操作不回滚，固定回复提示先查询状态；
- `model.end` 的字段统一，非流式 `token_chars` 恒为 0。

## 验收

```bash
conda run -n retail-agent python -m pytest -q
```

全量回归通过。另用离线模型替身对同一场景分别运行两条入口，比对最终回复、
消息历史和 Trace 事件序列：纯文本回答、域外闸门、工具预算超限三个场景均一致。
该比对已固化为 `tests/test_runtime.py` 的
`test_sync_and_stream_share_one_execution_path`。

## 边界

- 流式的 `GeneratorExit` 仍会把 `model.end` 标为 `cancelled`；非流式没有这个中断来源。
- `stream=True` 时若模型没有产出完整消息，抛 `RuntimeError`；非流式由模型适配层直接返回。
- 本次只改 `runtime.py` 与其回归用例，业务规则、确认状态机、HTTP 入口和前端均未改动。
