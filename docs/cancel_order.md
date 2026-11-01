# 用户确认后取消本人订单

对应 README 的 cancel_order。用户提供订单号与取消原因，系统准备并展示保存的摘要；
只有用户随后明确确认，运行时才调用业务服务，重新检查授权与规则后执行。
当前身份、订单和退款流水均为本地模拟数据，不接真实支付或银行，不发生真实资金划转。

## 支持范围

| 订单情况 | 行为 |
|---|---|
| 未付款，实付金额为0且无付款/发货/签收记录 | 确认后变为 canceled，不生成退款流水 |
| 已付款、无发货/签收记录，付款不超过24小时 | 确认后变为 canceled，生成一条 kind=cancellation、status=completed 的模拟退款流水 |
| 恰好付款满24小时 | 仍允许；超过24小时拒绝 |
| 已发货、已送达、已取消、已退款 | 拒绝 |
| 付款时间缺失、格式错误、无时区、在未来 | 拒绝，不猜测或补写历史付款时间 |
| 未付款状态与实付金额/付款记录不一致 | 拒绝 |
| 已有取消日志、取消退款流水，或处理中/已完成售后退款 | 拒绝新的取消请求，避免重复业务与资金结果 |

确认有效期为10分钟。准备时通过不代表执行时仍符合条件：确认时会复查当前订单状态、
付款时间、金额、退款记录和业务窗口；付款状态、时间或金额变化需要重新确认。
拒绝、过期或复查失败不改订单、不新增取消退款或取消日志。
复查失败时当前确认保留 pending，可先拒绝后重新发起，或等待到期。

## 运行

```powershell
conda run -n retail-agent python -m scripts.init_db
conda run --no-capture-output -n retail-agent python -m scripts.demo_chat
```

初始化新增两笔模拟订单，已有同号订单不会覆盖：

- ORD-3001：USER-001 的未付款订单，实付为0。
- ORD-3002：USER-001 的已付款订单，实付199元；首次加载时记录付款时间为当时UTC时间的一小时前。

重复初始化不会把 canceled 重置，也不会刷新已有付款时间。ORD-3002 超过窗口后会正常拒绝，
不要通过修改旧付款时间或删除数据库绕过规则。旧 ORD-1001 缺少付款时间，仍应拒绝取消。

出现历史菜单时选0开始新对话，体验：

```text
取消 ORD-3001，不需要了
取消后会怎样
算了，不取消了
取消 ORD-3001，不需要了
确认取消
```

预期先展示摘要而不取消，询问不执行，拒绝不执行；再次准备并确认后订单取消，无退款流水。

再新建对话体验已付款订单：

```text
取消 ORD-3002，不需要了
确认取消
```

若仍在窗口内，返回已取消、199元模拟取消退款流水及流水编号；订单状态为 canceled，
不会变成 refunded。缺少原因时应追问，模型不能编造原因。

准备后可输入exit退出，重新启动后按标题选择带“待确认取消”的记录继续。
选择菜单和恢复历史本身不会执行；过期授权不会因为恢复而重新生效。
对已经完成的取消恢复聊天历史，不会重放历史工具调用。

## 事务、幂等与日志

业务入口是 Service 的 approve_cancel_confirmation，仅由可信运行时在明确确认后调用。
模型工具 cancel_order 只接收 order_id/reason，执行器注入当前用户、会话和线程，
工具调用只准备确认；模型不能传用户身份、确认编号、金额、幂等键或批准标志。

业务服务在一个 BEGIN IMMEDIATE 事务中完成：

1. 按用户、会话、线程、确认编号读取记录，校验工具种类、参数指纹、有效期和状态。
2. 读取当前订单，重查归属、状态、付款时间、金额、24小时窗口及已有退款/取消记录。
3. 将订单更新为 canceled；已付款时插入模拟取消退款流水。
4. 写入 operation_logs，并把确认变为 consumed，记录 approved_at、consumed_at、result_json。

任一步写入异常，以上业务写入一起回滚；订单、流水、日志和确认结果不会只保存其中一部分。
业务事务与聊天历史快照仍分别提交，保存聊天历史失败不会撤销已提交的取消，也不会自动重做业务。

确认执行使用 confirm:<confirmation_id> 作为幂等键。重复调用同一确认返回首次保存的结果，
即使原有效期或付款窗口已过，也不再执行。不同确认竞争同一订单，最多一次业务成功。
同一确认参数与已提交的不可变日志指纹不同则返回 IDEMPOTENCY_CONFLICT。

operation_logs 本轮仅记录取消，绑定用户、订单与确认，保存原因、原状态、目标状态、金额、指纹和时间；
数据库触发器拒绝 UPDATE/DELETE。同一订单最多一条取消日志、一条取消退款流水。
迁移发现已有重复取消流水时会失败并回滚，不会擅自删除旧数据；需先核查冲突记录。
本轮的结果重放依托 confirmations.result_json，不等于已实现 README 中全部通用幂等及 LangGraph 恢复能力。

## 实际数据验收

```powershell
conda run -n retail-agent python -c "from app.storage.database import get_connection; c=get_connection(); print('orders:', [dict(r) for r in c.execute('SELECT order_id,status,paid_amount_cents,paid_at FROM orders WHERE order_id IN (?,?)', ('ORD-3001','ORD-3002'))]); print('refunds:', [dict(r) for r in c.execute('SELECT refund_id,order_id,kind,status,amount_cents,confirmation_id FROM refund_requests WHERE kind=?', ('cancellation',))]); print('logs:', [dict(r) for r in c.execute('SELECT order_id,confirmation_id,previous_status,new_status,amount_cents,reason FROM operation_logs')]); c.close()"
```

按具体确认编号定位：成功取消有一条 consumed 确认和一条日志；未付款没有退款流水，
已付款有一条同 confirmation_id 的 cancellation/completed 流水。
同一确认重复执行后数据条数不增加。可以通过订单详情查询核对 canceled 状态。
现有 get_refund_status 查询售后申请，不作为取消退款流水查询接口。

## 新增代码职责

| 文件 | 作用 |
|---|---|
| app/business/policies.py | 可注入时间的取消规则，含24小时边界与缺失时间拒绝 |
| app/business/cancellation.py | 取消业务服务、执行复查、原子写入、幂等结果重放 |
| app/business/service.py | 保持统一业务入口，转发取消资格检查与确认执行 |
| app/business/repository.py | 事务内订单更新、流水与日志插入、确认消费、历史检查 |
| app/storage/database.py | 取消业务唯一约束、日志表、归属外键和只追加触发器的增量迁移 |
| app/storage/seed.py、scripts/init_db.py | 可重复执行的取消演示订单加载，不覆盖已有状态和付款时间 |
| app/harness/confirmation.py | 保存取消摘要与参数快照，复用拒绝、过期和恢复 |
| app/harness/runtime.py | 按记录工具种类分发退款/取消批准，直接根据业务结果回复 |
| app/business/tools.py、app/harness/tool_executor.py | 注册cancel_order并只允许order_id/reason，可信上下文注入 |
| app/agent/confirmation_intent.py、app/agent/prompts.py | 根据保存摘要识别决定，区分取消与退款，缺参追问 |
| scripts/demo_chat.py | 历史菜单区分待确认取消/退款，恢复后继续处理并保存消息 |

## 自动化验收

```powershell
conda run -n retail-agent python -m pytest tests/test_cancellation.py
conda run -n retail-agent python -m pytest
```

本轮新增72个取消用例，全量225 passed, 4 subtests passed。
覆盖未付款/已付款、24小时边界、缺失/非法/未来时间、拒绝/过期/越权、指纹变化、
支付状态/金额变化、已有退款、任一步故障回滚、同一/不同确认并发、提交后响应丢失再调用、
日志不可修改、迁移冲突不删数据、重复加载、聊天入口和重启恢复。
测试仅替换联网模型响应，SQLite和业务事务真实执行；不证明真实模型能正确理解所有歧义表达。
