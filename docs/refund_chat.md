# 聊天中准备、确认和拒绝退款申请

当前功能：用户说明订单和原因 → 模型调用 prepare_refund_confirmation → 程序展示
已保存的待确认摘要 → 用户明确确认或拒绝 → Runtime 调用服务端处理并展示真实结果。

- 明确确认且复查通过：新增一条 pending 退款申请，确认记录变为 consumed。
- 明确拒绝：确认记录变为 rejected，不新增退款申请。
- 询问或含糊表达：继续等待，不提交申请。

本地身份和业务数据均为模拟数据。创建申请不代表退款到账；当前不支持审核或实际资金退款。

## 运行

```powershell
conda run -n retail-agent python -m scripts.init_db
conda run --no-capture-output -n retail-agent python -m scripts.demo_chat
```

依次输入：

```text
ORD-2002 商品破损，我想申请退款
确认后会怎样
算了，我不申请了
ORD-2002 商品破损，我想申请退款
确认提交
```

预期准备阶段显示订单、89.00 元金额、原因、确认编号和截止时间。
询问时继续等待，拒绝时显示“已拒绝当前操作，未创建退款申请”。
重新准备并确认后，返回申请编号、89.00 元金额和 pending 状态，并说明未代表退款到账。
在 pending 期间由独立模型请求识别 approve/reject/other；询问、含糊或不合法报告不触发提交。

该示例要求订单仍在退款窗口内且没有处理中申请。若已成功提交，重新申请会被拒绝，
不要删除已有业务数据绕过规则。初始化脚本保留已有数据，不会重置申请。

只问“能否退款”应使用资格查询；未提供原因时应追问，不可编造原因。
模型是否正确选择工具须在真实模型对话中验证，离线模型替身只能验证执行链路。

## 实际数据验收

```powershell
conda run -n retail-agent python -c "from app.storage.database import get_connection; c=get_connection(); print('confirmations:', [dict(r) for r in c.execute('SELECT confirmation_id,session_id,thread_id,order_id,status FROM confirmations ORDER BY rowid DESC LIMIT 5')]); print('refunds:', c.execute('SELECT COUNT(*) FROM refund_requests').fetchone()[0]); c.close()"
```

按启动时打印的会话、线程和返回的确认编号定位记录：拒绝后为 rejected；
成功提交后为 consumed，并有一条 confirmation_id 相同的退款申请，状态为 pending。
准备和拒绝不新增退款；一次成功提交新增一条退款申请。订单状态不因这些操作变化。
比较操作前后状态，不假设数据库始终只有默认模拟数据。

## 服务端提交与验证

approve_refund_confirmation 只由可信入口在用户明确确认后调用，不作为模型业务工具开放。
它在同一个 BEGIN IMMEDIATE 事务中校验确认上下文、状态、有效期和参数指纹，
重新读取订单并检查资格与金额，然后插入申请并保存确认执行结果。
任一步写入失败，申请和确认更新一起回滚。成功后重复调用同一确认编号返回保存的结果，
即使原确认有效期已过，也不会再次创建申请。

```powershell
conda run -n retail-agent python -m pytest
```

2026-10-31 验证结果：31 passed, 4 subtests passed。
离线测试覆盖事务连接、正常提交、重复调用、错误上下文、到期、已拒绝、订单状态/期限/金额变化、
另一线程先提交、参数与指纹不一致、写入失败回滚，以及聊天确认/拒绝/不合法报告和业务拒绝。
另一线程测试是先后提交，不代表已验证真正并发请求。
本地数据库观察到同一会话中的 rejected 与 consumed 记录，后者关联一条 8900 分的 pending 申请，
订单仍为 shipped；数据库记录不能单独证明真实模型对所有否定、引用或歧义都能正确分类。

## 职责和限制

- 工具参数只有 order_id 和 reason；模型传入身份、会话、金额或确认凭证会被拒绝。
- session_id、thread_id 在聊天启动时生成，由 Runtime 传给 ToolExecutor，再用于确认记录绑定。
- 执行器缺少会话或线程标识时返回 MISSING_CONTEXT，不写确认记录。
- 待确认阶段只向模型开放意图报告工具，提取 approve/reject/other 和本轮原话摘录。
  模型不提供用户、会话或确认 ID，执行对象来自数据库中当前会话的记录。
- 意图报告必须结构合法、只有一次调用、且决定依据逐字出现在本轮输入中；否则保留操作。
  原话匹配只能检查来源，不能保证语义正确；真实模型对否定、引用和歧义的表现仍需验收。
- Runtime 展示保存后的原始摘要，不再请求模型改写；同一批后续工具调用会补齐暂停结果但不执行。
- 成功提交回复由 Runtime 根据服务端结果生成，不再让模型改写申请编号、金额或状态。
- 资格或金额复查失败时不提交，当前实现保留 pending；金额变化后可先拒绝，再重新准备。
- 本轮同步提交在一个事务中将 pending 变为 consumed，并记录 approved_at，不单独保存 approved 阶段。
- 同一确认编号的结果重放由服务端支持；聊天尚未实现已完成操作的显式结果查询或重启恢复。
- 新消息到来或准备操作时，会在事务中将当前上下文内到期的 pending 标为 expired。
- 运行时默认使用真实 UTC 时间，可由可信调用方通过 now 参数注入参考时间。
  ORD-2002 在 2026-10-26T02:00:00+00:00 发货，超过七天后会被规则拒绝，不能保证永久可演示。
- 每次启动创建新会话，退出不自动拒绝旧操作；当前没有聊天恢复和后台过期清理。
- 尚未接入 LangGraph 中断和检查点，当前暂停通过数据库状态与现有 Python 循环实现。
