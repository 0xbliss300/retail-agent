# 聊天中准备和拒绝退款操作

当前功能：用户说明订单和原因 → 模型调用 prepare_refund_confirmation → 程序展示
已保存的待确认摘要 → 用户自然表达拒绝 → 确认状态变为 rejected，退款记录不增加。
本地身份和业务数据均为模拟数据。当前不支持批准、正式提交、审核或实际资金退款。

## 运行

```powershell
conda run -n retail-agent python -m scripts.init_db
conda run --no-capture-output -n retail-agent python -m scripts.demo_chat
```

依次输入：

```text
ORD-2002 商品破损，我想申请退款
算了，我不申请了
```

预期第一轮显示订单、89.00 元金额、原因、确认编号、截止时间和自然语言拒绝提示。
第二轮显示“已拒绝当前操作，未创建退款申请。”
在 pending 期间由独立模型请求识别本轮意图；明确拒绝才关闭操作，其他表达保留当前摘要。
例如“拒绝会怎么样”“别拒绝”“先等等”应保留 pending；当前没有批准能力。

只问“能否退款”应使用资格查询；未提供原因时应追问，不可编造原因。
模型是否正确选择工具须在真实模型对话中验证，离线模型替身只能验证执行链路。

## 实际数据验收

```powershell
conda run -n retail-agent python -c "from app.storage.database import get_connection; c=get_connection(); print('confirmations:', [dict(r) for r in c.execute('SELECT confirmation_id,session_id,thread_id,order_id,status FROM confirmations ORDER BY rowid DESC LIMIT 5')]); print('refunds:', c.execute('SELECT COUNT(*) FROM refund_requests').fetchone()[0]); c.close()"
```

按启动时打印的会话、线程和返回的确认编号定位记录，拒绝后应为 rejected。
退款数量应与操作前一致，默认模拟数据为 1 条；订单状态不应因准备或拒绝操作变化。

## 职责和限制

- 工具参数只有 order_id 和 reason；模型传入身份、会话、金额或确认凭证会被拒绝。
- session_id、thread_id 在聊天启动时生成，由 Runtime 传给 ToolExecutor，再用于确认记录绑定。
- 执行器缺少会话或线程标识时返回 MISSING_CONTEXT，不写确认记录。
- 待确认阶段只向模型开放意图报告工具，提取 reject/other 和本轮原话摘录。
  模型不提供用户、会话或确认 ID，拒绝函数仍按数据库中当前记录及可信上下文执行。
- 意图报告必须结构合法、只有一次调用、且拒绝依据逐字出现在本轮输入中；否则保留操作。
  原话匹配只能检查来源，不能保证语义正确；真实模型对否定、引用和歧义的表现仍需验收。
- Runtime 展示保存后的原始摘要，不再请求模型改写；同一批后续工具调用会补齐暂停结果但不执行。
- 新消息到来或准备操作时，会在事务中将当前上下文内到期的 pending 标为 expired。
- 运行时默认使用真实 UTC 时间，可由可信调用方通过 now 参数注入参考时间。
  ORD-2002 在 2026-10-26T02:00:00+00:00 发货，超过七天后会被规则拒绝，不能保证永久可演示。
- 每次启动创建新会话，退出不自动拒绝旧操作；当前没有聊天恢复和后台过期清理。
- 尚未接入 LangGraph 中断和检查点，当前暂停通过数据库状态与现有 Python 循环实现。
