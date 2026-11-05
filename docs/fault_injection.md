# 故障注入：提交后响应丢失

用户点了「确认取消」，订单真的取消了，退款流水也生成了，但响应在回来的路上丢了。
界面上什么都没有，用户不知道发生了什么，通常的做法是——再点一次。

这条功能要证明的就是：**再点一次不会变成取消两次**。

## 故障点为什么在提交之后

`decide_confirmation` 把业务写入和确认消费放在同一个事务里：

```text
BEGIN IMMEDIATE
  取消订单 / 生成退款流水 / 写操作日志
  confirmations.status = 'consumed'   （连同 result_json）
COMMIT
return result
```

响应是在 `COMMIT` 之后才产生的。所以故障点只有一个有意义的位置：函数已经返回、
数据库已经提交，但调用方没有拿到这个返回值。`FaultInjector` 就在这一刻抛出
`ResponseLost`，模拟连接断开。

放在事务中间没有意义——那测的是回滚，而回滚从来不是这条链路的弱点。

## 恢复路径

响应丢了之后，系统不做任何自动重试，而是让用户可以问：

1. `describe_recovery`（`POST /recover`）→ 应为 `executed`，并带上保存的 `result`；
2. 用户再点一次确认 → 走同一条 `decide_confirmation`，确认记录已是 `consumed`，
   业务层按 `result_json` 重放，返回历史结果，**不重新执行业务写入**；
3. 数据库里仍然是 1 条订单变更、1 条退款、1 条确认、1 条日志。

## 两条确认路径，两个注入位置

业务提交都发生在同一个事务里，但「响应」产生的位置不同：

| 确认方式 | 业务提交发生在 | 注入点 |
|---|---|---|
| 显式确认 | `decide_confirmation` 内 | 该函数返回之后 |
| 自然语言确认 | `_handle_pending_confirmation` 内 | 第二轮 `send_message` 返回之后 |

自然语言确认多了一层：模型先识别「确认取消」，runtime 识别意图后直接调用业务函数，
提交完成后才生成整轮回复。所以这条路径丢的是**整轮回复**，不是单个工具结果——
注入器包住的是 `send_message` 整体。

重放的验证方式也随路径不同：

- 显式确认：再调用一次 `decide_confirmation`，确认记录已是 `consumed`，业务层按 `result_json` 重放；
- 自然语言确认：恢复后已无待确认记录，再发一条消息只会得到普通回复、不构成重放。
  因此同样用 `decide_confirmation` 验证「同一确认编号不会被二次执行」。

两条路径共用同一组判定项，区别只在被包裹的调用。

## 用例格式

`eval/dev_cases.jsonl` 中的故障用例多一个 `fault` 字段：

```json
{
  "case_id": "fault_cancel_response_lost",
  "confirmation": {"mode": "explicit", "decision": "approve"},
  "fault": {"point": "after_commit"},
  "expected": {
    "outcome": "canceled",
    "order_status": "canceled",
    "refund_count": 1,
    "confirmation_count": 1,
    "operation_logs": 1,
    "recovery_state": "executed",
    "recovery_result_status": "canceled"
  }
}
```

约束由 `case_schema.py` 强制：

- `point` 目前只接受 `after_commit`；
- 必须包含确认动作（`mode` 不为 `none`），且确认意图为批准：
  拒绝路径不产生写入，注入没有意义；
- 声明了 `fault` 就必须声明 `recovery_state` 与 `recovery_result_status`，
  否则等于只注入故障、不验证恢复；
- 没有 `fault` 的用例不允许出现这两个字段，防止把两种路径混在一起。

`recovery_result_status` 必须与业务结果对应：`canceled` / `submitted` / `withdrawn`。

## 判定项

Oracle 的数据库检查（订单状态、退款条数、确认状态、日志）与普通用例完全相同。
在此之上追加 6 项内存判定：

| 检查 | 含义 |
|---|---|
| `fault.triggered` | 故障确实触发了，不是一次普通的成功路径 |
| `fault.fired_times` | 只丢一次响应，重放时不再丢 |
| `recovery.state` | 恢复查询返回 `executed` |
| `recovery.result_status` | 恢复查询给出的历史结果与业务结果一致 |
| `replay.error` | 重放没有报错，走的是幂等重放而不是拒绝 |
| `replay.no_new_write` | 重放前后四张业务表行数完全一致 |

`replay.no_new_write` 比较的是「提交之后」与「重放之后」两次快照，不是写入前后——
写入本身就是这次故障要保留的事实。

## 运行与验收

```powershell
conda run -n retail-agent python -m eval.run_eval --case fault_cancel_response_lost
conda run -n retail-agent python -m eval.run_eval --all
conda run -n retail-agent python -m pytest tests/test_fault_injection.py -q
```

四条用例覆盖三个写工具与两条确认路径：

| 用例 | 工具 | 恢复结果 |
|---|---|---|
| `fault_cancel_response_lost` | `cancel_order` | `canceled`，同时生成一笔取消退款流水 |
| `fault_refund_response_lost` | `prepare_refund_confirmation` | `submitted`，申请为 `pending`，订单不变 |
| `fault_withdraw_response_lost` | `withdraw_refund` | `withdrawn`，原申请保留、金额不变 |
| `fault_chat_cancel_response_lost` | `cancel_order`（自然语言确认） | `canceled`，验证整轮回复丢失后的恢复 |

`tests/test_fault_injection.py` 覆盖注入器本身（只丢一次、业务异常不触发、未知故障点报错）、
schema 的约束、四条端到端用例，以及两条变异测试：

- 把 `recovery_state` 改成 `idle`，判定必须失败；
- 把 `refund_count` 改成 2，Oracle 必须失败。

变异测试的作用是证明判定读的是数据库和恢复结果，不是复述 Agent 的回复。

## 当前限制

- 只实现「提交后响应丢失」一个故障点，超时和进程重启未覆盖。
- 自然语言确认只覆盖取消一条路径，退款申请与撤回只做了显式确认。
- 重放只验证「不重复写」与「返回历史结果」，不验证客户端收到重复响应时的展示逻辑。
- 只覆盖 4 条用例，不是 40 条冻结集里的故障分组。
