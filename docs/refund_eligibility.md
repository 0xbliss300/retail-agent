# 当前阶段：退款资格只读预检

调用路径：`service.check_refund_eligibility` → Repository 读取订单 →
Policy 检查状态和时间窗口 → Repository 查询处理中申请。

本地数据均为模拟数据。初始化可重复执行，不覆盖同 ID 的已有记录：

```powershell
conda run -n retail-agent python -m scripts.init_db
```

`ORD-2001` 已有 pending 申请；`ORD-2002` 无申请。两者发货时间均为
`2026-10-26T02:00:00+00:00`。使用固定参考时刻复现演示：

```powershell
conda run -n retail-agent python -c "from datetime import datetime; from app.business.service import check_refund_eligibility; print(check_refund_eligibility(user_id='USER-001', order_id='ORD-2002', now=datetime.fromisoformat('2026-10-28T02:00:00+00:00')))"
```

预期 `ok=True`、`eligible=True`、`policy_code=ELIGIBLE`，金额为 8900 分。
将订单号改为 `ORD-2001`，预期 `eligible=False`，规则码为
`ACTIVE_REFUND_EXISTS`；将用户改为 `USER-002`，预期 `ORDER_NOT_FOUND`。

- `ok=True` 表示检查完成；是否符合条件看 `eligible`。
- 无权访问与订单不存在均返回 `ok=False, error=ORDER_NOT_FOUND`。
- `now` 必须由可信调用方提供并包含时区；无时区时抛出 `ValueError`。
- 发货或签收后恰好 7 天允许，超过 1 秒拒绝；缺失、无效和未来时间拒绝。
- 状态或时间检查不通过时直接返回对应原因，不继续查询已有申请。
- 资格判断不写数据库，也没有接入模型工具列表。
- 预检结果不是写入凭证。当前分别读取订单和申请，不承诺并发下的快照一致性；
  后续创建申请须在写事务内复验归属、规则和已有申请，并落实确认与幂等。
- 尚未实现创建申请、审核和结算；现有 `get_order` 工具的接口保持不变。
