# 拒绝待确认操作

当前已接入聊天入口，用户用自然语言表达拒绝，由模型识别意图，再由运行时调用拒绝函数。
仍可直接调用 Harness 函数。
当前不批准确认，也不创建退款。聊天完整操作见 [待确认退款聊天](refund_chat.md)。

输入为可信调用方提供的 user_id、session_id、thread_id、confirmation_id 和带时区的 now。
数据库查询和更新均绑定上述身份及会话标识，其他上下文与不存在的记录统一返回
CONFIRMATION_NOT_FOUND。

- pending 且 now < expires_at：保存 rejected。
- pending 且 now >= expires_at：保存 expired，返回 CONFIRMATION_EXPIRED。
- rejected：重复调用仍返回 rejected，不重新写入。
- expired：重复调用仍返回 CONFIRMATION_EXPIRED。
- approved/consumed：返回 CONFIRMATION_NOT_PENDING，不改状态。
- 读取和更新使用同一个 BEGIN IMMEDIATE 事务，Repository 不自行提交。
- 到期关闭在读取当前会话操作及准备新操作时触发，尚无后台清理；只释放 pending，不处理 approved。
- expires_at 格式异常或缺少时区时抛出 ValueError，事务回滚，不猜测时间。

先初始化模拟数据，再运行演示：

```powershell
conda run -n retail-agent python -m scripts.init_db
conda run -n retail-agent python -m scripts.demo_reject
```

演示使用固定参考时刻 2026-10-29T02:00:00+00:00，调用真实 SQLite 和业务函数，
不请求模型。每次运行使用独立演示会话，新增两条模拟确认记录，最终分别为
rejected 和 expired，保留已有确认记录。订单及退款数据不变。

预期错误上下文均返回 CONFIRMATION_NOT_FOUND，两次拒绝均返回 rejected，
恰好到期及重复处理均返回 CONFIRMATION_EXPIRED；saved_states 显示两个终态，
business_data_unchanged 为 true。默认模拟数据的 refund_count 为 1。
