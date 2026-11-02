# 内部模拟退款结算

用户提交退款申请后，售后系统完成审核和退款；用户再次查询时，退款申请变为
`completed`，订单变为 `refunded`。本轮实现的是本地模拟结算，不接真实支付系统，
不发生真实资金划转。

`settle_refund` 是内部服务入口，不加入 `TOOLS`，模型无法调用，也不能传入金额、
订单归属或状态。Agent 只能通过 `get_refund_status` 和 `get_order` 读取结算后的结果。

## 状态变化

```text
refund_requests.status: pending -> completed
orders.status:          shipped | delivered -> refunded
```

退款申请金额必须等于订单实付金额；V1 只支持全额退款，不新增 `approved` 中间态。

## 支持与拒绝

| 情况 | 行为 |
|---|---|
| `after_sales`、`pending`，订单 `shipped`/`delivered`，金额等于实付 | 同一事务完成退款和订单状态 |
| 同一退款重复结算 | 返回首次保存结果，不重复写入 |
| 退款不存在 | `REFUND_NOT_FOUND` |
| 取消产生的 `cancellation` 流水 | `REFUND_KIND_NOT_SUPPORTED`，不写入 |
| 退款不是 `pending` | `REFUND_NOT_PENDING`，不写入 |
| 订单不是 `shipped`/`delivered` | `ORDER_STATUS_NOT_SUPPORTED`，不写入 |
| 退款金额与订单实付不一致 | `AMOUNT_MISMATCH`，不写入 |
| 退款已完成但缺少一致结算记录，或订单未变为 `refunded` | `STATE_INCONSISTENT` |

准备和批准退款申请仍然使用现有确认流程，见 [聊天中准备、确认和拒绝退款申请](refund_chat.md)。

## 输入与输出

输入由可信内部入口提供：

```text
refund_id + 带时区的 now
```

输出示例：

```json
{
  "ok": true,
  "refund_id": "rf_...",
  "order_id": "ORD-2003",
  "amount_cents": 6900,
  "refund_status": "completed",
  "order_status": "refunded",
  "replayed": false
}
```

重复调用时除 `replayed` 为 `true` 外，返回同一结果。

## 运行

先加载可重复执行的模拟数据：

```powershell
conda run -n retail-agent python -m scripts.init_db
```

查找当前待结算的售后申请：

```powershell
conda run -n retail-agent python -c "from app.storage.database import get_connection; c=get_connection(); print([dict(r) for r in c.execute('SELECT refund_id,order_id,amount_cents FROM refund_requests WHERE kind = ? AND status = ?', ('after_sales','pending'))]); c.close()"
```

选择返回中的一个 `refund_id` 执行结算：

```powershell
conda run -n retail-agent python -m scripts.settle_refund --refund-id <refund_id>
```

预期返回 `refund_status=completed`、`order_status=refunded`、`replayed=false`。

再次执行同一命令，预期返回相同业务结果且 `replayed=true`，数据库记录数不增加。

## 数据验收

```powershell
conda run -n retail-agent python -c "from app.storage.database import get_connection; c=get_connection(); rid='<refund_id>'; print('refund:', dict(c.execute('SELECT refund_id,order_id,status,amount_cents,reason,updated_at FROM refund_requests WHERE refund_id = ?', (rid,)).fetchone())); print('order:', dict(c.execute('SELECT order_id,status,paid_amount_cents FROM orders WHERE order_id = (SELECT order_id FROM refund_requests WHERE refund_id = ?)', (rid,)).fetchone())); print('settlements:', [dict(r) for r in c.execute('SELECT * FROM refund_settlements WHERE refund_id = ?', (rid,))]); c.close()"
```

预期：

- 退款申请 `status=completed`，金额和原因不变；
- 订单 `status=refunded`；
- 恰好一条 `refund_settlements` 记录，保存退款、订单、金额、前后状态和时间；
- 不新增退款申请，不新增退款流水。

结算记录由数据库触发器保护，`UPDATE` 和 `DELETE` 会被拒绝。

## 聊天查询

启动聊天入口：

```powershell
conda run --no-capture-output -n retail-agent python -m scripts.demo_chat
```

选择新对话后输入：

```text
查一下 ORD-2003 的订单状态
ORD-2003 的退款进度怎么样？
```

若结算的是 `ORD-2003` 的售后申请，预期分别返回订单已退款（`refunded`）和退款申请已完成（`completed`）。
聊天入口只读取数据库，不会再次执行结算。

## 事务与幂等

`settle_refund` 使用一个 `BEGIN IMMEDIATE` 事务：

1. 读取退款申请及关联订单；
2. 校验退款种类、状态、订单状态和金额；
3. 把退款申请从 `pending` 改为 `completed`；
4. 把订单从 `shipped`/`delivered` 改为 `refunded`；
5. 写入只追加的 `refund_settlements` 记录。

任一步失败都会回滚，不会出现退款完成但订单仍为已发货，或订单已退款但退款申请仍为待处理的部分状态。
重复调用先读取已保存的结算记录并重放结果，不再修改订单。

## 当前限制

- 不接真实支付、银行或退款审核系统，所有资金结果均为本地模拟。
- 没有管理员身份和权限系统；`scripts/settle_refund.py` 只是受信任的内部模拟入口，不应通过 HTTP 或模型暴露。
- 不支持部分退款、`approved` 中间态或失败重试流程。
- 结算后订单为终态 `refunded`，不能再次申请退款或取消。