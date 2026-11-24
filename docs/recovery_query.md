# 断线后的操作状态查询

用户发出「取消订单」后网络中断、客户端崩溃或服务重启，界面上的结果没有回来。
这时用户唯一想知道的是：**那笔操作到底执行了没有**。

服务端其实一直知道答案——业务写入与确认记录在同一个事务里提交，不存在「写了一半」的中间态。
缺的只是一个让客户端把答案问出来的入口。`POST /recover` 就是这个入口。

## 三种状态

| `state` | 含义 | `result` |
|---|---|---|
| `executed` | 已执行，数据库已变更 | 保存的完整结果，例如 `{"status": "canceled", "refund": {...}}` |
| `awaiting_confirmation` | 尚未执行，仍等待用户决定 | `null`，`summary` 与 `expires_at` 有值 |
| `idle` | 没有未决操作 | `null` |

`idle` 还会带 `last_state`，用于区分三种结束方式：

| `last_state` | 含义 |
|---|---|
| `null` | 当前账号和会话下未找到对应操作 |
| `rejected` | 用户主动拒绝 |
| `expired` | 确认超时，不会再执行 |
| `consumed` | 已执行（此时 `state` 是 `executed`） |

`expired` 是查询时按当前时间判断得出的，**不会写回数据库**；
真正的状态落地发生在下一次正常对话里。

## 为什么是只读

查询接口必须可以安全地重复调用、被客户端在重连循环里轮询。
一旦它有写副作用（比如顺手把过期记录标记为 `expired`），重试与并发就会变得难以推理。

所以 `describe_recovery` 只读：打开连接、读一条记录、关闭。不改任何状态。

## 接口

```bash
curl -s -b cookies.txt http://127.0.0.1:8000/recover \
  -H "Content-Type: application/json" \
  -d '{"session_id":"sess_xxx","thread_id":"thread_xxx","confirmation_id":"cfm_xxx"}'
```

响应：

```json
{
  "state": "executed",
  "last_state": "consumed",
  "tool": "cancel_order",
  "order_id": "ORD-2002",
  "summary": "取消订单 ORD-2002，原因：不想要了。执行后订单状态变为已取消。",
  "expires_at": "2026-11-04T12:55:00+00:00",
  "result": {
    "ok": true,
    "status": "canceled",
    "order_id": "ORD-2002",
    "refund": {"refund_id": "rf_...", "kind": "cancellation", "amount_cents": 8900, "status": "completed"}
  }
}
```

使用同一账号登录时保存的 Cookie 文件。保存方法见 [README 确认接口](../README.md#103-确认接口)。
请求体接受对话编号和可选的 `confirmation_id`。省略确认编号时查询该对话最近一条确认记录。
换用户或换 `thread_id` 查不到别人的记录，统一返回 `idle`，不泄露操作是否存在。

## 运行与验收

```powershell
conda run -n retail-agent python -m pytest tests/test_recovery.py -q
```

覆盖的检查：

- 从未操作过 → `state=idle`、`last_state=null`；
- 已准备未确认 → `awaiting_confirmation`，带摘要与过期时间；
- 已批准执行 → `executed`，`result.status=canceled`，退款金额正确；
- 用户拒绝 → `idle`、`last_state=rejected`；
- 确认过期 → `idle`、`last_state=expired`；
- 连续查询两次数据库内容完全一致（只读）；
- 其他用户查询同一会话 → `idle`；
- HTTP 接口三种状态与空参数（422）。

## 与确认流程的关系

`/confirm` 是「我现在决定」，`/recover` 是「刚才那件事怎么样了」。
两者都不调用模型，都不需要重新解释自然语言。

客户端推荐的调用顺序：

1. 取消确认前，将 `pending_action.confirmation_id` 和服务器对话编号一同保存到当前账号的网页对话记录，再调用 `/confirm`。
2. 刷新、重开对话或点击「重新查询」时，用原编号调用 `/recover`。服务端按登录身份、会话和确认编号检索。
3. `executed` 显示取消结果和模拟退款流水。`awaiting_confirmation` 恢复确认卡，再次确认沿用原编号。查询失败或找不到记录时，可重新查询。

## 网页模拟验收

从项目根目录运行以下命令。演示服务使用临时 SQLite 数据库和离线模型替身，订单取消、确认、查询和退款流水走项目的实际实现。

```powershell
npm --prefix frontend run build
conda run --no-capture-output -n retail-agent python -m scripts.demo_cancel_recovery
```

1. 打开 `http://127.0.0.1:8765`，用 `demo@retailops.local` / `demo1234` 登录。
2. 发送「取消 ORD-3002，不需要了」，然后点击「确认取消订单」。演示服务提交数据库事务后丢弃第一次取消响应。
3. 终端出现 `ORD-3002 canceled，模拟退款流水 1 条` 后刷新浏览器。也可以等网页显示「暂未查到结果」再刷新。
4. 确认卡变成「订单取消结果」，显示 `ORD-3002` 和「已取消」。结果卡的模拟退款流水号应与终端打印的一致。

停止演示服务后，本次数据库随临时目录清理。重启演示服务即可用同一订单重新演示。

## 当前限制

- 每次查询返回一条确认记录。网页恢复入口随当前浏览器标签页的最近对话保留。
- `idle` 不区分「没有操作」与「操作早已结束」，需要看 `last_state`。
- 不做自动重试：恢复只负责告诉客户端发生了什么，重试与否由调用方决定。
