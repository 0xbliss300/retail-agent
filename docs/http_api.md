# HTTP 对话接口（`/health`、`/chat`、`/chat/stream`）

本轮把现有 CLI 聊天能力接到 FastAPI：

- `GET /health`：健康检查；
- `POST /chat`：发送一条用户消息，一次性返回回复、会话编号和待确认操作；
- `POST /chat/stream`：用 SSE 流式返回文本，结束时返回会话编号和待确认操作；
- 确认和拒绝暂时仍在对话中用自然语言表达，独立的 `/confirm` 接口尚未实现。

HTTP 入口使用服务端固定模拟身份 `USER-001`，请求体不能传 `user_id`。这不是多用户登录，
只适合本地演示，不能直接暴露到公网。

## 启动

先初始化数据库，再启动服务：

```powershell
conda run -n retail-agent python -m scripts.init_db
conda run --no-capture-output -n retail-agent uvicorn app.main:app --reload --port 8000
```

服务启动后访问：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method Get | ConvertTo-Json -Depth 5
```

> Windows PowerShell 里的 `curl` 是 `Invoke-WebRequest` 的别名，不支持 `-H`、`-d` 这类参数。
> 下面的示例使用 `Invoke-RestMethod`；如果在 Git Bash 中运行，也可以使用 `curl`。

预期：

```json
{
  "status": "ok",
  "demo_user_id": "USER-001",
  "version": "0.1.0"
}
```

## 开始新对话

```powershell
$body = @{ message = "查询我的订单" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8000/chat" -Method Post -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) | ConvertTo-Json -Depth 5
```

如果 PowerShell 的引号或编码不方便，也可以直接使用 Python 客户端：

```powershell
conda run -n retail-agent python -c "import httpx; print(httpx.post('http://127.0.0.1:8000/chat', json={'message': '查询我的订单'}).json())"
```

请求体字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `message` | 是 | 用户本轮消息，1～4000 字符 |
| `session_id` | 否 | 恢复已有对话时提供，必须和 `thread_id` 一起出现 |
| `thread_id` | 否 | 恢复已有对话时提供，必须和 `session_id` 一起出现 |

首次请求不传 `session_id` 和 `thread_id`，服务端生成并返回：

```json
{
  "reply": "你当前有这些订单……",
  "session_id": "sess_...",
  "thread_id": "thread_...",
  "pending_action": null,
  "history_saved": true,
  "warning": null
}
```

## 继续同一对话

```powershell
$body = @{
    message = "查看 ORD-4002 的详情"
    session_id = "sess_..."
    thread_id = "thread_..."
} | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8000/chat" -Method Post -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) | ConvertTo-Json -Depth 5
```

服务端会加载已保存的消息和工具调用关系，继续调用模型；每轮完成后保存完整历史。

聊天历史自最后一次保存起 10 分钟内可恢复。超过期限且没有待确认操作时返回
`CONVERSATION_NOT_FOUND`，需要开始新对话。

## 流式输出

`POST /chat/stream` 使用 Server-Sent Events（SSE），请求体与 `/chat` 完全相同。
`/chat` 仍然一次性返回 JSON，不受影响。

事件格式：

```text
event: token
data: {"text":"你当前有"}

event: token
data: {"text":"这些订单……"}

event: done
data: {"reply":"你当前有这些订单……","session_id":"sess_...","thread_id":"thread_...","pending_action":null,"history_saved":true,"warning":null}
```

事件说明：

| 事件 | 含义 |
|---|---|
| `token` | 模型文本增量；客户端按顺序拼接 |
| `done` | 本轮结束，包含完整回复、会话编号、待确认操作和历史保存状态 |
| `error` | 模型调用失败；HTTP 状态此时已经是 200，客户端应停止并提示错误 |

流式实现的边界：

- 只流式输出文本。工具调用的参数会先完整缓冲，再执行工具，不会逐字输出工具参数。
- 如果模型先输出一段文字再调用工具，这段文字会先到达客户端，工具执行后继续输出后续文字。
- 取消/退款仍先返回待确认摘要；`done.pending_action` 包含确认编号，确认和拒绝仍通过下一条对话消息表达。
- 历史在本轮流结束后保存；`history_saved=false` 时按 `/chat` 的同样规则处理，不要重复提交业务操作。
- 客户端中途断开时，服务端不保证保存本轮历史；已经提交的订单或退款不会因为断开而撤销。
- 当前不输出模型隐藏推理、工具参数增量，也不使用 WebSocket。

启动服务后，在另一个终端运行流式演示：

```powershell
conda run --no-capture-output -n retail-agent python -m scripts.demo_stream --message "查询我的订单"
```

继续已有对话：

```powershell
conda run --no-capture-output -n retail-agent python -m scripts.demo_stream --message "查看 ORD-4002 的详情" --session-id "sess_..." --thread-id "thread_..."
```

脚本会边接收边打印 `token`，最后打印 `done` 事件的完整 JSON。

## 高风险操作

用户要求取消订单或申请退款时，`/chat` 返回 `pending_action`；`/chat/stream` 在
`done` 事件中返回同样的 `pending_action`。

```json
{
  "reply": "取消订单 ORD-4001……\n确认编号：cfm_...\n本次取消尚未执行……",
  "session_id": "sess_...",
  "thread_id": "thread_...",
  "pending_action": {
    "confirmation_id": "cfm_...",
    "tool": "cancel_order",
    "order_id": "ORD-4001",
    "summary": "取消订单 ORD-4001……",
    "status": "pending",
    "expires_at": "2026-11-02T13:30:00+00:00"
  },
  "history_saved": true,
  "warning": null
}
```

此时订单和退款数据没有变化，只生成了待确认记录。用户下一条消息说“确认取消”或
“确认提交”后，服务端重新校验授权、状态、期限和参数，再执行写入。

独立的 `/confirm` 接口尚未实现；当前确认由模型识别用户意图，服务端仍会重新校验业务条件。

## 历史保存失败

`history_saved=false` 表示本轮回复已经产生，但聊天历史没有保存。常见原因：

- 同一对话被另一个窗口更新；
- 数据库写入失败。

此时 `reply` 仍然返回，因为业务操作可能已经提交。`warning` 会提示先查询订单或退款状态，
不要因为历史保存失败而重复提交。服务端不会自动重试业务操作。

## 错误响应

| HTTP 状态 | `detail.error` | 含义 |
|---:|---|---|
| 400 | `INVALID_SESSION_CONTEXT` | `session_id` 和 `thread_id` 没有同时提供，或为空 |
| 404 | `CONVERSATION_NOT_FOUND` | 对话不存在、已过期，且没有待确认操作 |
| 422 | `EMPTY_MESSAGE` | `message` 只有空白 |
| 422 | Pydantic 校验错误 | 缺少 `message`、消息过长，或包含 `user_id` 等额外字段 |
| 502 | `MODEL_ERROR` | 模型服务不可用 |

错误响应不会返回堆栈、SQL、API Key 或他人订单。

## 身份与限制

- 身份固定在服务端：`DEMO_USER_ID = USER-001`，请求体不能覆盖。
- `session_id` 和 `thread_id` 只用于定位当前模拟用户的对话，不是登录凭证。
- 当前没有多用户登录、令牌签名或按用户隔离的 HTTP 会话；不要把服务直接暴露给外部用户。
- 没有 `/confirm`、前端、LangGraph Checkpointer、Trace 或评测入口。
- 所有订单、退款和资金结果均为本地模拟数据。

## 测试

```powershell
conda run -n retail-agent python -m pytest tests/test_api.py
conda run -n retail-agent python -m pytest
```