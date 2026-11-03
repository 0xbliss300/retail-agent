# 单轮对话 Trace

本轮给一次对话建立可追踪的执行记录：模型调用、工具调用、确认操作和整轮结果都追加写入 JSONL。
它解决的问题是：超时、重试或确认失败后，能从记录里还原“哪一步慢、哪个工具失败、重试了几次”，而不是只看最终回复。

## 输出位置

默认写入：

```text
data/traces.jsonl
```

每行一个 JSON 事件，追加写入。`data/traces.jsonl` 已在 `.gitignore` 中，不会进入版本库。

## 事件类型

| 事件 | 来源 | 主要字段 |
|---|---|---|
| `agent.start` | 对话入口 | `run_id`、`source`、`session_id`、`thread_id`、`message_chars` |
| `agent.end` | 对话入口 | `run_id`、`status`、`duration_ms`、`reply_chars`、`history_saved`、`pending` |
| `model.start` | Agent 运行时 | `run_id`、`step`、`message_count` |
| `model.end` | Agent 运行时 | `run_id`、`step`、`status`、`duration_ms`、`tool_call_count`，流式还有 `token_chars` |
| `tool.call` | 工具执行边界 | `run_id`、`tool`、`ok`、`status`、`error`、`attempts`、`duration_ms` |
| `confirmation.prepared` | 工具执行边界 | `run_id`、`tool`、`order_id`、`expires_at` |
| `confirmation.intent` | 自然语言确认路径 | `run_id`、`tool`、`intent` |
| `confirmation.decision` | 自然语言或显式确认 | `run_id`、`source`、`tool`、`decision`、`status`、`error`；显式确认还带 `session_id`、`thread_id` |

`agent.end.status` 可能是：

- `ok`：正常结束；
- `error`：未预期异常；
- `model_error`：流式模型调用失败；
- `cancelled`：客户端中途断开。

`tool.call.attempts` 表示实际执行次数：只读工具超时或数据库忙时可能是 2，写工具始终是 1。

## 示例

```json
{"ts":"2026-11-03T10:00:00+00:00","event":"agent.start","run_id":"run_...","source":"chat","session_id":"sess_...","thread_id":"thread_...","message_chars":4}
{"ts":"2026-11-03T10:00:00+00:00","event":"model.start","run_id":"run_...","step":0,"message_count":2}
{"ts":"2026-11-03T10:00:00.812+00:00","event":"model.end","run_id":"run_...","step":0,"status":"ok","duration_ms":812.4,"tool_call_count":1}
{"ts":"2026-11-03T10:00:00.815+00:00","event":"tool.call","run_id":"run_...","tool":"get_order","ok":true,"status":"ok","error":null,"attempts":1,"duration_ms":3.1}
{"ts":"2026-11-03T10:00:01.600+00:00","event":"agent.end","run_id":"run_...","source":"chat","status":"ok","duration_ms":1600.0,"reply_chars":42,"history_saved":true,"pending":false}
```

## 配置

通过进程环境变量控制：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TRACE_ENABLED` | `true` | `false`、`0`、`no`、`off` 关闭 |
| `TRACE_PATH` | `data/traces.jsonl` | Trace 文件路径 |

Trace 写入是尽力而为：目录不存在会自动创建；磁盘、权限或序列化失败只忽略，不影响聊天和业务写入。

## 不记录的内容

- 完整用户消息和模型回复，只记录字符数；
- 退款/取消原因等自由文本；
- 完整工具参数；
- `confirmation_id` 等确认凭证；
- API Key、Authorization Header 和模型隐藏推理。

## 查看方式

```bash
tail -n 20 data/traces.jsonl
```

按 `run_id` 过滤一轮对话：

```bash
grep '"run_id":"run_你的编号"' data/traces.jsonl
```

## 测试

```bash
conda run -n retail-agent python -m pytest tests/test_tracing.py -q
```

覆盖：

- JSONL 追加写入；
- 关闭 Trace 和写入失败时不抛异常；
- 一轮查询产生 `model.start/end` 和 `tool.call`；
- 超时重试在 `tool.call.attempts` 中体现；
- `send_message` 产生 `agent.start/end`；
- `confirmation.prepared` 不记录确认凭证。

测试默认关闭 Trace，避免向 `data/traces.jsonl` 写入临时事件。

## 限制

- JSONL 追加写入，暂不轮转、压缩或清理。
- 只覆盖 Agent 边界事件，不记录业务 Service 内部的每条 SQL。
- 当前没有 LangGraph Checkpointer 事件，也没有评测 Case/Baseline 字段。
- Trace 是本地开发辅助，不是审计或合规日志。