# 查询本人订单列表与详情

用户无需先知道订单号，可以要求 Agent 列出本人订单，再选择某笔订单查看详情。
README 原先已规划并实现按订单号查询详情，本轮新增 `list_orders` 作为只读扩展，复用已有 `get_order`。
不增加数据库表、不改变现有订单数据，也不更改原详情查询的返回格式。

## 运行

```powershell
conda run -n retail-agent python -m scripts.init_db
conda run --no-capture-output -n retail-agent python -m scripts.demo_chat
```

出现历史菜单时选择 `0` 开始新对话，依次输入：

```text
查询我的订单
查看 ORD-2002 的详情
查一下 ORD-1002 的详情
```

默认本地身份是模拟用户 USER-001。若数据未修改，其列表包含 ORD-1001、ORD-2001、ORD-2002、ORD-2003，
不包含其他用户的 ORD-1002。详情应按实际数据返回状态、实付金额和时间；
查询他人订单与不存在的订单统一返回 ORDER_NOT_FOUND。
也可以根据刚才返回的列表说“查看第2个订单详情”，模型应使用列表里的实际订单号。
实际模型是否正确选择工具和理解指代，仍需通过真实聊天验收。

## 工具契约

`list_orders` 不接收订单号或用户身份，只接受可选分页参数：

```json
{"limit": 2, "offset": 0}
```

返回：

```json
{
  "ok": true,
  "orders": [
    {
      "order_id": "ORD-1001",
      "status": "paid",
      "paid_amount_cents": 9900,
      "paid_at": null,
      "shipped_at": null,
      "delivered_at": null
    }
  ],
  "limit": 2,
  "offset": 0,
  "has_more": false,
  "next_offset": null
}
```

上例仅说明单条记录时的返回结构，不表示默认模拟用户只有一笔订单。

- 默认每页10条，limit只能是1～50的整数，offset必须为非负且不超出SQLite整数范围的整数；布尔值不接受。
- 按订单号升序分页；先过滤用户再分页。列表不包含内部 user_id，也不接受客户端指定身份。
- 查询时多读取一条记录判断 has_more，不执行全表订单计数。
- has_more=true时，下一页使用相同limit和返回的next_offset，不把本页条数当成订单总数。
- 首页面为空表示本人暂无订单；offset大于0时的空页仅表示当前页没有记录。
- 返回状态、实付金额（单位为分）和订单时间；时间为空时不推测，未付款订单实付金额可为0。

`get_order(order_id)` 继续使用原有身份校验，返回本人订单基本详情。
最近退款摘要尚未合并到get_order；退款进度通过已有get_refund_status查询。
当前数据均为本地模拟数据，入口固定使用USER-001，尚未接入真实登录认证或电商订单源。
本轮不增加订单状态筛选、全量统计或批量业务操作。

## 新增代码的作用

```text
“我的订单” → 模型调用 list_orders
           → ToolExecutor 检查允许的参数并注入 user_id
           → Service 校验分页范围
           → Repository 按用户过滤、排序和分页
           → 模型展示结果
选择订单   → 复用 get_order 再次校验归属并返回详情
```

| 文件 | 作用 |
|---|---|
| `app/business/repository.py` | 新增 get_orders_for_user，使用参数化SQL按用户过滤分页。 |
| `app/business/service.py` | 新增 list_orders，校验身份和分页，返回 has_more/next_offset。 |
| `app/business/tools.py` | 声明 list_orders 的模型契约并转发服务。 |
| `app/harness/tool_executor.py` | 拒绝额外身份/订单号参数，身份来自程序可信上下文。 |
| `app/agent/prompts.py` | 区分列表与详情、指导翻页及订单选择，不为列表请求追问订单号。 |
| `tests/test_my_orders.py` | 验证本人范围、分页、空结果、参数边界、零写入及聊天中列表转详情。 |

## 验收

```powershell
conda run -n retail-agent python -m pytest tests/test_my_orders.py
conda run -n retail-agent python -m pytest
```

本人订单查询接入时新增32个测试，当时全量结果为153 passed, 4 subtests passed；后续功能会增加全量用例数。
自动化仅替换联网模型响应，工具执行、Service、Repository和SQLite真实运行。
工具查询不写数据库；聊天入口保存对话历史是已有行为，测试单独核对业务数据不变。
