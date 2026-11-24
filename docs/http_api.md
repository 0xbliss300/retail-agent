# HTTP 对话接口（`/health`、`/chat`、`/chat/stream`）

本轮把现有 CLI 聊天能力接到 FastAPI：

- `GET /health`：健康检查；
- `POST /chat`：发送一条用户消息，一次性返回回复、会话编号和待确认操作；
- `POST /chat/stream`：用 SSE 流式返回文本，结束时返回会话编号和待确认操作；
- `POST /confirm`：显式批准或拒绝待确认操作，不调用模型。

订单、退款、聊天、确认和恢复接口都从登录会话取得当前用户身份。首次使用时，
运行初始化脚本创建[两个本地演示账号](frontend.md#两个账号查单)。

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
  "version": "0.1.0"
}
```

## 登录

`POST /auth/login` 接收 `email` 和 `password`，校验通过后设置有效期为 8 小时的
`retailops_session` Cookie。后续请求使用同一个客户端。
`GET /auth/me` 获取当前账号，`POST /auth/logout` 撤销登录会话。

```powershell
$base = "http://127.0.0.1:8000"
$credentials = @{ email = "demo@retailops.local"; password = "demo1234" } | ConvertTo-Json
Invoke-RestMethod -Uri "$base/auth/login" -Method Post -ContentType "application/json" -Body $credentials -SessionVariable loginSession
```

## 开始新对话

```powershell
$body = @{ message = "查询我的订单" } | ConvertTo-Json
Invoke-RestMethod -Uri "$base/chat" -Method Post -WebSession $loginSession -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) | ConvertTo-Json -Depth 5
```

如果 PowerShell 的引号或编码不方便，也可以直接使用 Python 客户端：

```python
import httpx

with httpx.Client(base_url="http://127.0.0.1:8000") as client:
    client.post("/auth/login", json={
        "email": "demo@retailops.local",
        "password": "demo1234",
    }).raise_for_status()
    print(client.post("/chat", json={"message": "查询我的订单"}).json())
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
Invoke-RestMethod -Uri "$base/chat" -Method Post -WebSession $loginSession -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) | ConvertTo-Json -Depth 5
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

脚本会提示输入账号密码，登录后边接收边打印 `token`，最后打印 `done` 事件的完整 JSON。
用 `--email demo2@retailops.local` 可选择第二个账号。

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

此时订单和退款数据没有变化，只生成了待确认记录。

等待确认时，在同一个 `session_id` 和 `thread_id` 继续发送问题即可。助手可以解释
当前操作的影响，或查询订单、物流、退款进度和政策。操作仍可使用原确认编号批准或拒绝，
有效期按首次发起时计算。若操作在回答期间到期，响应的 `pending_action` 为 `null`。

确认有两种方式：

- **显式接口（推荐给前端）**：调用 `POST /confirm`，直接提交 `approve` 或 `reject`；
- **文字指令**：在 `/chat` 或 `/chat/stream` 单独发送与当前操作对应的指令，例如“确认取消”“确认提交”或“确认撤回”。“确认取消后会怎样？”属于插问。

两条路径使用同一套确认记录和业务服务，服务端都会重新校验上下文、有效期、订单状态和金额。

## 显式确认（`/confirm`）

前端拿到 `pending_action` 后，优先用按钮调用 `/confirm`，不再依赖模型判断用户意图。

```powershell
$body = @{
    session_id = "sess_..."
    thread_id = "thread_..."
    confirmation_id = "cfm_..."
    decision = "approve"
} | ConvertTo-Json
Invoke-RestMethod -Uri "$base/confirm" -Method Post -WebSession $loginSession -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) | ConvertTo-Json -Depth 5
```

`decision` 只允许：

| 值 | 行为 |
|---|---|
| `approve` | 批准取消或退款申请；执行前重新校验业务条件 |
| `reject` | 拒绝当前操作；订单和退款数据不变 |

返回结构与 `/chat` 相同：

```json
{
  "reply": "订单 ORD-4001 已取消。……",
  "session_id": "sess_...",
  "thread_id": "thread_...",
  "pending_action": null,
  "history_saved": true,
  "warning": null
}
```

行为约定：

- 批准取消：订单变为 `canceled`；已付款时生成模拟取消退款流水。
- 批准退款：创建 `pending` 售后申请，订单状态不变。
- 拒绝：确认记录变为 `rejected`，不写订单、退款或操作日志。
- 重复调用同一确认：返回首次保存的结果，不重复写业务数据。
- 已过期、已消费、政策不允许或订单状态变化：不执行，返回解释性回复。
- `/confirm` 不创建模型客户端、不调用模型，只做服务端校验和事务执行。
- 确认编号必须属于登录用户及当前 `session_id`、`thread_id`。

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
| 401 | `LOGIN_REQUIRED` | 登录会话缺失、失效，或页面账号已切换 |
| 401 | `INVALID_CREDENTIALS` | 邮箱或密码错误 |
| 404 | `CONVERSATION_NOT_FOUND` | 对话不存在、已过期，且没有待确认操作 |
| 404 | `CONFIRMATION_NOT_FOUND` | 当前会话中不存在该确认编号 |
| 409 | `CONFIRMATION_INVALID` | 确认记录的工具或参数无效 |
| 422 | `EMPTY_MESSAGE` | `message` 只有空白 |
| 422 | `INVALID_CREDENTIALS` | 登录字段缺失或格式无效 |
| 422 | `INVALID_ARGUMENTS` | `/confirm` 的会话或确认编号为空 |
| 422 | Pydantic 校验错误 | 缺少字段、`decision` 不是 `approve/reject`，或包含 `user_id` 等额外字段 |
| 502 | `MODEL_ERROR` | 模型服务不可用 |

过期的确认不会返回 4xx，而是返回 200 和说明“操作已过期”的 `reply`；此时业务数据没有变化。

错误响应不会返回堆栈、SQL、API Key 或他人订单。

## 身份与限制

- 服务端用 Cookie 查找登录会话和账号。对话编号用于定位该账号下的对话。
- 预置账号、订单、退款和资金结果用于本地演示。

## 测试

```powershell
conda run -n retail-agent python -m pytest tests/test_api.py tests/test_streaming.py tests/test_confirm_api.py
conda run -n retail-agent python -m pytest
```
