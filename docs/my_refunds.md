# 本人退款申请列表

用户不需要提供订单号，就能看到自己提交过哪些退款申请、各自处于什么状态。
这是售后场景中最常见的开场问题（“我有几笔退款在处理？”）的直接入口。

## 与单笔查询的区别

| 工具 | 适用问题 | 是否需要订单号 |
|---|---|---|
| `list_refunds` | 我有哪些退款申请？有几笔还在处理？ | 不需要 |
| `get_refund_status` | 这笔订单的退款到哪一步了？ | 需要 |

两者不可互相替代：列表给出全局概览，单笔查询给出某订单的最新申请。

## 返回内容

每条申请包含：申请编号、订单号、金额、状态、用户填写的原因、申请时间、
最后更新时间，以及该订单当前状态。

状态取值：

| 状态 | 含义 |
|---|---|
| `pending` | 已提交，等待处理 |
| `withdrawn` | 用户已撤回，可重新申请 |
| `completed` | 已完成，订单同时变为 `refunded` |
| `rejected` | 平台审核拒绝 |
| `approved` | 已批准，当前实现不会产生 |
| `failed` | 处理失败，当前实现不会产生 |

列表只包含售后申请（`kind = 'after_sales'`），**不包含取消订单产生的退款流水**
（`kind = 'cancellation'`）。取消是订单终止，不是用户提交的退款申请。

## 输入与输出

```json
{ "status": "pending", "limit": 10, "offset": 0 }
```

- `status` 可选，不传表示全部状态；
- `limit` 默认 10，范围 1–50；
- `offset` 默认 0，下一页使用返回的 `next_offset`；
- `user_id` 由运行时注入，模型传入会被拒绝。

输出示例：

```json
{
  "ok": true,
  "refunds": [
    {
      "refund_id": "rf_...",
      "order_id": "ORD-2002",
      "amount_cents": 8900,
      "status": "pending",
      "reason": "商品破损",
      "created_at": "2026-11-04T08:12:00+00:00",
      "updated_at": "2026-11-04T08:12:00+00:00",
      "order_status": "shipped"
    }
  ],
  "status": "pending",
  "limit": 10,
  "offset": 0,
  "has_more": false,
  "next_offset": null
}
```

`refunds` 为空表示该用户没有对应状态的申请，不表示没有订单。

## 运行

```powershell
conda run --no-capture-output -n retail-agent python -m scripts.demo_chat
```

选择新对话后输入：

```text
我有哪些退款申请？
我有哪些还没处理的退款？
我的退款都处理得怎么样了
```

预期：

- 第一条返回本人全部售后申请，按申请时间倒序；
- 第二条只返回 `pending`，不包含 `withdrawn` 与 `completed`；
- 第三条按状态分别说明，不编造审核进度或到账时间。

## 数据验收

```powershell
conda run -n retail-agent python -c "from app.storage.database import get_connection; c=get_connection(); print([dict(r) for r in c.execute('SELECT refund_id,order_id,status,amount_cents FROM refund_requests WHERE user_id = ? AND kind = ? ORDER BY created_at DESC', ('USER-001','after_sales'))]); c.close()"
```

预期列表条数与聊天入口返回的 `refunds` 条数一致（未分页时）；
其他用户的申请不出现在结果中；`cancellation` 流水不出现在结果中。

## 排序与分页

- 排序：`created_at` 倒序，同一时间按写入顺序倒序，保证稳定；
- 分页：先多读一条判断 `has_more`，因此 `refunds` 最多 `limit` 条；
- `has_more = true` 时只展示当前页，不能把当前页数量当作申请总数。

## 当前限制

- 只读，不提供按订单号批量查询或按时间范围筛选。
- 不返回审核意见、到账时间或资金流水明细。
- `approved` 与 `failed` 状态在当前实现中不会产生，仅为读取兼容保留。
