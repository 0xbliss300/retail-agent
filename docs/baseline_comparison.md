# B/C 对照实验

本实验比较两个版本在同一批售后场景下的结果：

| 版本 | 用户归属与业务规则 | 用户确认 | 服务端幂等 | 审计日志 |
|---|---|---|---|---|
| B · Rule-Guarded | 有 | **无**，直接执行 | 无 | 无（受外键约束） |
| C · Reliable Agent | 有 | 有 | 有 | 有 |

B 档只存在于 `baselines/rule_guarded.py` 与评测入口，生产 API 固定使用 C 档。

## 为什么不复现 A 档

A 档（Raw ReAct，无授权、无规则）要求存在一条跳过用户归属过滤的工具路径。
当前生产代码里没有这样的入口，也没有任何场景需要它。为对照实验人为造一条，
会让仓库长期带着一个可被误用的旁路，风险大于它能提供的证据价值。

代价必须说清楚：**本实验无法量化「授权与业务规则」本身的收益**，
只能量化「确认、幂等与恢复」的收益。报告中不出现 A 档数字，也不以估算代替。

## 实验设计

- 用例：`eval/baseline_cases.jsonl`，三条取消场景（已付款、未付款、已发货）。
- 时钟：固定 `2026-11-03T10:00:00+00:00`，结果可重复。
- 隔离：每条用例使用独立 SQLite 文件，互不影响。
- 判定：两档共用 `eval/oracle.py`，只读数据库，不读 Agent 回复。
- C 档用 `eval/dev_cases.jsonl` 中同 `case_id` 的用例，保证场景一致。

## 指标定义

| 指标 | 定义 |
|---|---|
| 业务结果达成率 | 订单最终状态等于该场景应有结果的比例，两档同一标准 |
| Oracle 通过率 | 各档完整断言（含确认记录数量）通过的比例 |
| 未确认写入率 | 业务状态发生变化、但数据库中没有 `consumed` 确认记录的比例 |

「未确认写入」由 `eval/oracle.py:detect_unconfirmed_write` 直接查库判定：
订单变为 `canceled`/`refunded`，或存在退款记录，同时该订单没有已消费的确认记录。

## 运行

```powershell
conda run -n retail-agent python -m eval.run_eval --compare
```

也可以只跑 B 档：

```powershell
conda run -n retail-agent python -m eval.run_eval --baseline b
```

产物：

```text
reports/runs/<timestamp>/compare/
├── comparison.json          # 两档汇总与逐条配对结果
├── baseline_comparison.md   # 同一份报告
├── b/<case_id>/result.json
└── c/<case_id>/result.json
```

同时会把报告写到仓库根的 `reports/baseline_comparison.md`。

## 当前实测结果

| 指标 | B | C |
|---|---:|---:|
| 业务结果达成率 | 100.0% | 100.0% |
| Oracle 通过率 | 100.0% | 100.0% |
| 未确认写入率 | 66.7% | 0.0% |

解读要点：

1. **业务结果一致**。没有确认这一层，B 同样能完成取消，差别不在结果，
   而在用户是否被询问过。只看成功率会得出「B 不比 C 差」的错误结论。
2. **未确认写入率是唯一能区分两档的指标**。B 的两笔成功取消都没有确认记录。
3. **B 写不了审计日志**。`operation_logs` 对 `confirmations` 有复合外键，
   没有确认记录就插不进去。这不是实现遗漏，而是确认机制缺失的直接后果——
   而审计日志正是事后追责的依据。

## 局限

- 只有三条取消场景，且只覆盖 `cancel_order`；退款与撤回的 B 档未实现。
- 未覆盖重复写入。B 档仍受 `refund_requests` 上 `kind='cancellation'` 唯一索引约束，
  服务端幂等层面的收益需要独立的重复提交用例才能量化。
- 未覆盖故障注入与恢复，因此「恢复能力」的收益没有进入本表。
- 脚本化模型，不是真实 LLM。B 与 C 的模型行为完全相同，差异只来自服务端装配。
