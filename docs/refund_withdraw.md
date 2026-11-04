# 撤回尚未处理的退款申请

用户提交退款申请后、内部结算前，可以撤回该申请。撤回只结束这一条申请，
不改变订单状态；撤回后订单仍为 `shipped` 或 `delivered`，用户可以重新提交申请。

撤回与「拒绝退款」不是同一件事：`rejected` 表示平台审核不通过，
`withdrawn` 表示用户主动收回申请。两者在 `get_refund_status` 中会分别呈现。

## 状态变化

```text
refund_requests.status: pending -> withdrawn
orders.status:          不变（shipped 或 delivered）
confirmations.status:   pending -> consumed
```

只有 `pending` 的售后申请可以撤回。`approved`、`completed`、`failed`、
`rejected` 和 `withdrawn` 都不能再次撤回。

## 支持与拒绝

| 情况 | 行为 |
|---|---|
| 本人订单、存在 `pending` 的 `after_sales` 申请 | 生成待确认撤回操作 |
| 用户确认 | 同一事务把申请置为 `withdrawn`，订单状态不变 |
| 用户拒绝 | 确认记录置为 `rejected`，申请保持 `pending` |
| 确认过期 | 确认记录置为 `expired`，申请保持 `pending` |
| 订单没有售后申请 | `NO_ACTIVE_REFUND` |
| 申请已进入处理或已结束 | `REFUND_NOT_PENDING`，返回当前状态 |
| 撤回与内部结算同时到达 | 先到者生效；后到者读到非 `pending` 状态并拒绝 |
| 同一确认重复提交 | 返回首次保存结果，不再修改申请 |
| 跨用户或跨会话提交确认编号 | `CONFIRMATION_NOT_FOUND`，不写入 |

## 输入与输出

输入由运行时提供，模型只能提供订单号：

```text
user_id + session_id + thread_id + order_id + 带时区的 now
```

准备阶段的输出：

```json
{
  "ok": true,
  "status": "awaiting_confirmation",
  "tool": "cancel_refund",
  "confirmation_id": "cfm_...",
  "summary": "撤回订单 ORD-2003 的退款申请 rf_...，金额 69.00 元。",
  "expires_at": "2026-11-04T08:37:46+00:00"
}
```

批准后的输出：

```json
{
  "ok": true,
  "status": "withdrawn",
  "refund_id": "rf_...",
  "order_id": "ORD-2003",
  "amount_cents": 6900
}
```

## 运行

启动聊天入口：

```powershell
conda run --no-capture-output -n retail-agent python -m scripts.demo_chat
```

选择新对话后依次输入：

```text
ORD-2003 的退款进度怎么样？
把 ORD-2003 的退款申请撤回吧
确认撤回
```

预期：

- 第一步返回 `pending`（待处理）；
- 第二步返回撤回摘要与确认编号，提示申请尚未撤回；
- 第三步返回申请已撤回，订单状态未改变。

撤回后再次查询该订单，`get_refund_status` 返回 `withdrawn`；
再次要求退款会重新走「准备 → 确认」流程。

## 数据验收

```powershell
conda run -n retail-agent python -c "from app.storage.database import get_connection; c=get_connection(); print(dict(c.execute('SELECT refund_id,order_id,status,amount_cents FROM refund_requests WHERE order_id = ?', ('ORD-2003',)).fetchone())); print(dict(c.execute('SELECT order_id,status FROM orders WHERE order_id = ?', ('ORD-2003',)).fetchone())); print([dict(r) for r in c.execute('SELECT confirmation_id,tool,status FROM confirmations WHERE order_id = ?', ('ORD-2003',))]); c.close()"
```

预期：

- 申请 `status=withdrawn`，金额与原因不变；
- 订单仍为 `shipped`（或原本的 `delivered`）；
- 对应的确认记录 `tool=cancel_refund`、`status=consumed`；
- 不新增退款申请，不新增结算记录。

撤回后调用 `settle_refund` 会返回 `REFUND_NOT_PENDING`，不会把订单改成 `refunded`。

## 事务与幂等

`approve_withdraw_confirmation` 使用一个 `BEGIN IMMEDIATE` 事务：

1. 读取并校验确认记录归属当前用户、会话和线程；
2. 校验保存参数的指纹与幂等键，不接收模型新传入的值；
3. 在同一事务内复查申请仍属于当前用户、仍为 `pending`、金额未变；
4. 把申请改为 `withdrawn`；
5. 把确认记录置为 `consumed` 并保存结果。

任一步失败都会回滚，不会出现申请已撤回但确认记录仍为待确认，
或确认已消费但申请仍为待处理的部分状态。

撤回与结算共用 `status = 'pending'` 作为更新前提，两边的 `UPDATE` 只有一方能成功：
数据库行锁保证先提交者生效，后到者拿到 `rowcount = 0` 并返回 `REFUND_NOT_PENDING`。

## 数据库迁移

`refund_requests.status` 与 `confirmations.tool` 的取值集合都扩展了：

- `refund_requests.status` 新增 `withdrawn`；
- `confirmations.tool` 新增 `cancel_refund`。

SQLite 不能修改 `CHECK` 约束，`migrate_withdraw_refund` 通过「建新表 → 搬数据 →
换名 → 重建索引」完成升级。迁移期间外键在事务外关闭，提交前执行
`PRAGMA foreign_key_check`，有任何悬空引用都会回滚。函数可重复执行：
已经包含新取值的库不会再重建。

## 当前限制

- 不接真实支付与审核系统，撤回仅改变本地模拟状态。
- 只支持撤回 `pending` 申请；`approved` 中间态在当前实现中不会产生。
- 撤回不记录独立审计日志表；审计依据为确认记录中的参数指纹与结果。
