<div align="center">

<h1>RetailOps Agent</h1>

<p><strong>面向电商售后业务的可靠执行 Agent</strong></p>

<p>用 LangGraph 编排工具调用，用 SQLite 保存真实业务状态，<br/>
用确认、幂等与恢复保证高风险写入安全，并以 A/B/C 受控对照实验量化服务端保护的实际收益。</p>

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white" />
  <img alt="LangGraph" src="https://img.shields.io/badge/LangGraph-1.x-1C3C3C" />
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white" />
  <img alt="SQLite" src="https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite&logoColor=white" />
  <img alt="License" src="https://img.shields.io/badge/License-TBD-lightgrey" />
</p>

<p>
  <a href="#1-项目功能">项目功能</a> ·
  <a href="#2-系统架构">系统架构</a> ·
  <a href="#3-业务模型">业务模型</a> ·
  <a href="#4-六个业务工具">工具契约</a> ·
  <a href="#5-可靠性设计">可靠性设计</a> ·
  <a href="#6-评测设计">评测设计</a> ·
  <a href="#7-项目结构">项目结构</a> ·
  <a href="#9-快速开始">快速开始</a> ·
  <a href="#10-使用说明">使用说明</a> ·
  <a href="#11-常见问题">常见问题</a>
</p>

</div>

## 1. 项目功能

RetailOps Agent 是一个面向电商售后业务的执行型 Agent。

它不是“只会聊天”的客服机器人。它要解决的是：

- 用户用自然语言提出售后请求；
- Agent 能正确理解、查询、判断并执行；
- 退款、取消等高风险动作必须经过用户确认；
- 网络超时、响应丢失或进程重启后不能重复退款；
- 评测必须直接检查数据库副作用，而不是相信 Agent 自己说“已完成”。

覆盖的售后场景：

| 场景 | 示例 |
|---|---|
| 订单查询 | “帮我查一下 ORD-1001 的状态” |
| 物流查询 | “ORD-1003 的快递到哪了？” |
| 政策查询 | “退款需要满足什么条件？” |
| 退款资格判断 | “这个订单还能退吗？” |
| 取消订单 | “我要取消 ORD-1002” |
| 创建退款申请 | “ORD-1016 有破损，我要退款” |
| 多轮澄清 | 用户未提供订单号时追问 |
| 确认与恢复 | 用户确认后执行；超时或响应丢失后安全恢复 |

核心特点：

1. **业务规则在服务端执行**，不依赖 Prompt。
2. **高风险写操作必须确认**，未确认时数据库零写入。
3. **业务写入幂等**，重复请求和并发请求不会产生重复退款。
4. **故障可恢复**，提交后响应丢失也不会重复执行。
5. **评测基于数据库状态**，使用独立 Oracle 判定成功与违规。
6. **A/B/C 受控对照**，区分模型能力与服务端保护带来的收益。

---

## 2. 系统架构

请求从 HTTP 进入，经运行时控制、工作流编排、业务服务三层，最终落到两个独立的 SQLite 文件。

```text
Client
  │
  ▼
FastAPI
  ├── POST /chat
  ├── POST /confirm
  └── GET  /health
  │
  ▼
AgentRuntime
  ├── thread_id / session / 执行预算
  ├── ToolExecutor       参数校验、用户上下文、超时
  ├── Confirmation       待确认操作、确认 ID、过期、恢复
  ├── Recovery           重试决策、幂等结果查询
  └── Tracer             模型、工具、确认、恢复事件
  │
  ▼
LangGraph StateGraph
  ├── Reason             模型理解与决策
  ├── Prepare            参数校验、业务预检
  ├── Confirm            高风险操作中断等待确认
  ├── Execute            调用工具
  └── Respond            生成最终回复
  │
  ▼
Business Service
  ├── Authorization      订单归属与用户隔离
  ├── Policy             六态迁移、取消/退款规则
  ├── Idempotency        请求指纹、重复检测、结果复用
  ├── Transaction        订单、退款、确认、日志原子提交
  └── Audit              操作日志
  │
  ▼
SQLite
  ├── retail.db          用户、订单、物流、退款、确认、幂等、日志
  └── checkpoints.db     LangGraph Checkpointer
```

### 2.1 分层职责

每一层只回答一个问题，越界即视为设计缺陷：

```text
Prompt     负责“建议做什么”
Schema     负责“参数是否合法”
Service    负责“是否授权、是否符合规则、是否重复”
Repository 负责“如何安全读写”
Oracle     负责“最终数据库状态是否正确”
```

由此带来的直接结论：

- 业务规则不写在 Prompt 里。修改 Prompt 不会改变任何一条业务判定。
- 模型无法决定“自己是谁”。`user_id`、确认凭证、幂等键由运行时注入，模型传入会被拒绝。
- 评测不读取 Agent 的回复内容作为成功依据。回复只是给人看的产物，判定看数据库。

### 2.2 一次写操作的完整路径

```text
用户请求
  │
  ▼
Schema 校验 ────────── 参数不合法 → 返回结构化错误，无写入
  │
  ▼
业务预检 ───────────── 无权限/状态不符/超窗口 → 拒绝，无写入
  │
  ▼
创建待确认操作 ─────── 绑定用户、会话、线程、订单、参数指纹、过期时间
  │
  ▼
LangGraph 中断 ─────── 等待用户决定
  │
  ├── 拒绝 ────────── 标记 rejected，无写入
  ├── 超时/过期 ───── 标记 expired，无写入
  └── 批准 ────────── 重新校验授权与规则
                        │
                        ▼
                      单事务写入：订单 + 退款 + 操作日志 + 幂等记录
```

---

## 3. 业务模型

### 3.1 六态订单状态机

订单状态固定为六个：

```text
unpaid → paid → shipped → delivered
   │        │
   └────────┴──────────────→ canceled

shipped / delivered ──(内部结算)──→ refunded
```

| 状态 | 含义 | 说明 |
|---|---|---|
| `unpaid` | 未支付 | 可取消，无资金退款 |
| `paid` | 已支付、未发货 | 24 小时内可取消 |
| `shipped` | 已发货 | 可申请退款 |
| `delivered` | 已送达 | 签收后 7 天内可申请退款 |
| `canceled` | 已取消 | 终态 |
| `refunded` | 已退款 | 终态，只能由内部结算进入 |

约束：

- `canceled` 和 `refunded` 不允许回退。
- `paid → shipped`、`shipped → delivered` 由模拟仓储/系统动作推进，不由 Agent 直接执行。
- 已发货/已送达订单不能取消，只能申请退款。
- 已付款订单取消后，订单为 `canceled`，同时生成一条 `kind='cancellation'` 的退款流水；不会把订单标成 `refunded`。
- 未付款订单取消没有退款流水。

### 3.2 退款申请与退款完成

退款申请和订单状态是两条不同的生命周期：

```text
create_refund
    │
    ▼
refund_requests.status = pending
订单仍为 shipped / delivered
    │
    ▼
内部审核 / 结算（非 Agent 工具）
    │
    ▼
refund_requests.status = completed
订单变为 refunded
```

关键语义：

- `create_refund` **不会改变订单状态**。
- 创建成功只表示退款申请已提交，不代表款项已退。
- 同一订单最多一条 `pending/approved` 的售后退款申请。
- V1 只支持全额退款，金额由服务端根据实付金额计算，模型不能传入金额。
- Agent 在申请为 `pending` 时，只能回复“申请已提交”，不能声称“已退款”。

### 3.3 取消与退款时间窗口

| 操作 | 条件 |
|---|---|
| 取消未付款订单 | 可取消；需要确认 |
| 取消已付款订单 | 付款后 24 小时内、未发货；需要确认 |
| 取消已发货/已送达订单 | 不允许 |
| 申请退款（已发货） | 发货后 7 天内 |
| 申请退款（已送达） | 签收后 7 天内 |
| 重复取消/重复退款 | 拒绝 |
| 已有处理中退款 | 拒绝新的退款申请 |

时间窗口通过可注入的测试 Clock 计算，保证评测可复现。

---

## 4. 六个业务工具

| 工具 | 类型 | 输入 | 输出 | 副作用 |
|---|---|---|---|---|
| `get_order` | 只读 | 订单 ID | 订单信息、最近退款摘要 | 无 |
| `get_logistics` | 只读 | 订单 ID | 承运商、运单号、轨迹 | 无 |
| `check_refund_eligibility` | 只读 | 订单 ID | 是否可退、规则码、原因 | 无 |
| `get_policy` | 只读 | 政策主题 | 政策文本、版本 | 无 |
| `cancel_order` | 高风险写 | 订单 ID、原因 | 取消结果、退款流水 | 更新订单、写日志 |
| `create_refund` | 高风险写 | 订单 ID、原因 | 退款申请 ID、状态、金额 | 创建 `pending` 申请 |

安全约定：

- 模型不可传入 `user_id`、`confirmation_id`、`idempotency_key`。
- 用户身份、确认凭证、幂等键由 Runtime Harness 注入。
- 所有订单相关工具都校验当前用户是否拥有该订单。
- 跨用户访问统一返回 `ORDER_NOT_FOUND`，不泄露订单是否存在。
- 写工具只能在确认通过、授权复验和规则复验后执行。

---

## 5. 可靠性设计

### 5.1 可信身份与授权

- 用户身份来自服务端会话或签名令牌，不来自 LLM 输出。
- 所有读取和写入都按当前用户过滤。
- 跨用户访问返回统一安全错误，并记录 `auth.denied` Trace。
- 待确认操作执行前再次校验归属，防止确认后订单被转移。

### 5.2 用户确认

高风险操作执行流程：

```text
用户请求
  │
  ▼
Schema 校验
  │
  ▼
业务预检（授权 + 状态 + 规则）
  │
  ▼
创建 pending confirmation
  ├── 用户 / 会话 / thread
  ├── 工具 / 订单 / 参数指纹
  ├── confirmation_id / 过期时间
  └── 幂等键
  │
  ▼
LangGraph interrupt
  │
  ├── 用户拒绝 → 无写入
  ├── 用户确认 → 重新校验 → 执行
  └── 超时/过期 → 无写入
```

确认支持：

- 拒绝确认；
- 重复确认（返回历史结果，不重复执行）；
- 过期确认；
- 跨进程恢复；
- 跨用户/跨会话确认 ID 拒绝；
- 参数指纹不匹配拒绝。

### 5.3 幂等

- 幂等键作用域为 `(user_id, idempotency_key)`。
- 业务写入和幂等结果在同一个 SQLite 事务中提交。
- 同键同参数重放返回已提交结果。
- 同键不同参数返回 `IDEMPOTENCY_CONFLICT`，无副作用。
- 不同幂等键也不能绕过“同一订单只能有一条有效退款”的业务约束。
- 确认执行使用 `confirm:<confirmation_id>` 作为幂等键，由运行时生成，不暴露给模型。

### 5.4 故障恢复

恢复先判断状态，再决定是否重试：

| 情况 | 处理 |
|---|---|
| 写操作未开始 | 可安全重试 |
| 事务中失败 | 回滚，可重试 |
| 提交成功但响应丢失 | 按幂等键查询结果，复用 |
| 结果未知 | 先查幂等记录、操作日志和业务数据库 |
| 不确定且没有幂等键 | 不盲目重放 |
| 进程重启 | 使用 Checkpointer 和 `thread_id` 恢复 |

Checkpointer 使用独立 SQLite 文件，避免与业务数据库混在一起。

### 5.5 可观测性

Trace 采用 JSONL，记录：

- `run_id`、`session_id`、`case_id`、`baseline_id`；
- 模型调用；
- 工具名称、校验后的参数、耗时、结果；
- 确认创建、批准、拒绝、过期、消费；
- 中断、恢复、重试；
- 操作日志、幂等键、最终业务状态。

Trace 不记录 API Key、完整个人信息或模型隐藏推理过程。

---

## 6. 评测设计

评测的意义在于：成功与否不取决于 Agent 的自我描述，而取决于数据库的最终状态。

### 6.1 Evaluation Harness

评测流程：

```text
加载 Case
  │
  ▼
创建隔离数据库 + Checkpointer
  │
  ▼
加载模拟用户/订单/退款/物流
  │
  ▼
User Simulator 驱动多轮对话
  │
  ├── 用户补充参数
  ├── 用户确认/拒绝
  └── Fault Injector 注入超时/响应丢失/中断
  │
  ▼
保存 Trace 与数据库快照
  │
  ▼
Oracle 独立判定
  │
  ▼
Metrics / Reporter
```

评测原则：

- 每个 Case、每个 Baseline 使用独立数据库。
- 使用固定测试时钟和随机种子。
- User Simulator 只执行预设脚本，不提示正确工具或答案。
- Fault Injection 可复现，并记录是否触发。
- Oracle 直接读 SQLite，不调用 Agent、不调用 LLM、不读取 Agent 自评。
- 成功必须同时满足期望数据库状态和禁止副作用。
- 冻结测试集不用于 Prompt 调参。

### 6.2 40 条冻结测试

| 类别 | 数量 | 覆盖内容 |
|---|---:|---|
| 正常订单/物流/政策查询 | 8 | 只读、多轮、政策查询 |
| 取消场景 | 7 | 合规/不合规、时间窗口、拒绝确认 |
| 退款场景 | 7 | shipped/delivered、超窗口、已有申请、拒绝确认 |
| 越权访问 | 6 | 跨用户读写、Prompt 注入、伪造身份/确认 ID |
| 缺参与多轮澄清 | 5 | 缺订单号、意图不明、非法 ID、缺原因 |
| 重复请求与幂等 | 4 | 同键重放、冲突、不同键重复、并发 |
| 故障注入与恢复 | 3 | 超时、提交后响应丢失、进程重启 |
| **合计** | **40** | |

### 6.3 A/B/C 对照

| 能力 | A · Raw ReAct | B · Rule-Guarded | C · Reliable Agent |
|---|---|---|---|
| 工具调用 | 是 | 是 | 是 |
| 参数 Schema | 基础 | 是 | 是 |
| 服务端授权/规则 | 否（仅评测隔离环境） | 是 | 是 |
| 业务预检 | 否 | 是 | 是 |
| 用户确认 | 否 | 否 | 是 |
| 幂等写入 | 否 | 否 | 是 |
| 持久化 Checkpointer | 否 | 否 | 是 |
| 超时/响应丢失恢复 | 否 | 否 | 是 |

生产 API 固定使用 C。A 的“无规则/无授权”能力只存在于 eval-only 的隔离环境，不能通过普通环境变量在 API 中启用。

三个版本的差异构成实验的对照组：A 到 B 的增量是服务端规则与授权，B 到 C 的增量是确认、幂等、持久化与恢复。这样才能把“模型变强了”和“服务端兜住了”分开计量。

### 6.4 核心指标

| 指标 | 定义 |
|---|---|
| `task_success_rate` | Oracle 通过次数 / 总执行次数 |
| `policy_violation_rate` | 出现禁止副作用或缺少必要确认的执行次数 / 总执行次数 |
| `wrong_tool_rate` | 未知工具、Schema 失败、业务拒绝、超时的工具调用数 / 总工具调用数 |
| `duplicate_write_count` | 同一目标操作中超过第一次有效提交的写入次数之和 |
| `avg_tool_calls` | 总工具调用次数 / 总执行次数 |
| `avg_latency` | 总耗时 / 总执行次数，另报告 p50/p95 |

辅助指标：违规尝试数、服务端拦截数、确认合规率、恢复成功率、平均推理步数、Token 用量（真实模型时）。

---

## 7. 项目结构

```text
retail-agent/
│
├── app/                              # Agent 应用与业务执行系统
│   ├── __init__.py
│   ├── main.py                       # FastAPI 入口：/chat、/confirm、/health
│   ├── config.py                     # 模型、数据库、超时、步数、确认 TTL 配置
│   ├── schemas.py                    # Pydantic 契约：工具参数、响应、确认请求
│   │
│   ├── agent/                        # Agent 核心：理解、决策与工作流
│   │   ├── __init__.py
│   │   ├── graph.py                  # LangGraph 工作流、节点与条件路由
│   │   ├── state.py                  # AgentState：消息、订单、确认、错误、状态
│   │   ├── nodes.py                  # 推理、工具准备、确认、执行、响应节点
│   │   ├── prompts.py                # 系统提示词、工具规范、Prompt 版本
│   │   └── model_client.py           # 统一模型适配；真实模型与离线 Mock
│   │
│   ├── harness/                      # Runtime Harness：执行控制
│   │   ├── __init__.py
│   │   ├── runtime.py                # 启动、继续、结束、预算、线程、Trace
│   │   ├── tool_executor.py          # 参数校验、上下文注入、超时、结果封装
│   │   ├── confirmation.py           # 待确认操作、确认 ID、确认/拒绝/过期/恢复
│   │   └── recovery.py               # 重试决策、幂等结果查询、恢复协调
│   │
│   ├── business/                     # 业务层：关键安全约束
│   │   ├── __init__.py
│   │   ├── policies.py               # 六态迁移、取消/退款规则、Policy 版本
│   │   ├── service.py                # 授权、规则、事务、幂等执行、内部结算
│   │   ├── repository.py             # 订单、物流、退款、确认、幂等、日志
│   │   ├── idempotency.py            # 请求指纹、重复检测、结果复用
│   │   └── tools.py                  # 六个 Agent 工具接口与 Service 调用
│   │
│   ├── storage/                      # SQLite 与工作流持久化
│   │   ├── __init__.py
│   │   ├── database.py               # 连接、建表、事务、约束
│   │   ├── models.py                 # User、Order、RefundRequest 等实体
│   │   ├── seed.py                   # 模拟用户、订单、物流、退款数据
│   │   └── persistence.py            # LangGraph SQLite Checkpointer
│   │
│   └── observability/                # 可观测性
│       ├── __init__.py
│       └── tracing.py                # 模型、工具、确认、恢复、异常事件
│
├── baselines/                        # A/B/C 对照实验配置
│   ├── __init__.py
│   ├── react.py                      # A：原始 ReAct，无完整服务端保护
│   ├── rule_guarded.py               # B：ReAct + Schema + 授权 + 业务规则
│   └── reliable.py                   # C：B + 确认 + 持久化 + 幂等恢复
│
├── eval/                             # Evaluation Harness
│   ├── __init__.py
│   ├── harness.py                    # 评测主控制器：加载、执行、判定、保存
│   ├── run_eval.py                   # CLI：Baseline、Case、次数、输出目录
│   ├── case_schema.py                # Case Schema：输入、初始状态、期望、故障
│   ├── fixtures.py                   # 每个 Case/基线独立数据库与 Checkpointer
│   ├── user_simulator.py             # 多轮用户脚本、参数补充、确认/拒绝
│   ├── fault_injection.py            # 超时、响应丢失、进程中断
│   ├── oracle.py                     # 独立判定：数据库状态与禁止副作用
│   ├── metrics.py                    # 成功率、违规率、错误工具、延迟
│   ├── reporter.py                   # 汇总结果、对比报告、失败案例
│   ├── dev_cases.jsonl               # 开发集：允许调试和修改
│   └── test_cases.jsonl              # 40 条冻结测试：正式评测
│
├── tests/                             # 确定性单元测试与集成测试
│   ├── conftest.py                   # Fixture、临时数据库、测试配置
│   ├── test_storage.py               # 表结构、约束、事务回滚、append-only 日志
│   ├── test_tools.py                 # 六个工具的参数、输出、错误
│   ├── test_policy.py                # 取消、退款、六态迁移
│   ├── test_authorization.py         # 身份隔离与跨用户越权
│   ├── test_idempotency.py           # 重复、冲突、并发防重复写入
│   ├── test_confirmation.py          # 绑定、拒绝、过期、恢复
│   ├── test_recovery.py              # 超时、响应丢失、跨进程恢复
│   ├── test_graph.py                 # 节点、路由、中断、步数限制
│   └── test_oracle.py                # Oracle 不误判成功/失败
│
├── data/                              # 本地运行数据，不存真实个人信息
│   └── fixtures/
│       └── seed_orders.json          # 可版本化的模拟订单/用户数据
│
├── reports/                           # 评测结果与实验分析输出
│
├── scripts/                           # 本地开发辅助命令
│   ├── init_db.py                    # 初始化/重置模拟业务数据库
│   └── demo.py                       # 演示三个典型售后链路
│
├── .env.example                       # 环境变量示例，不含真实 API Key
├── .gitignore                         # 排除密钥、数据库、缓存、临时结果
├── pyproject.toml                     # 依赖、项目元数据、pytest/Ruff/mypy
└── README.md                          # 项目说明
```

---

## 8. 模块职责

### 8.1 包与子模块

| 包 | 职责 | 关键模块 |
|---|---|---|
| `app` | 应用入口、配置与对外契约 | `main.py`、`config.py`、`schemas.py` |
| `app.agent` | 模型理解与 LangGraph 工作流编排 | `graph.py`、`nodes.py`、`state.py`、`prompts.py`、`model_client.py` |
| `app.harness` | 执行控制：参数注入、确认、恢复、预算 | `runtime.py`、`tool_executor.py`、`confirmation.py`、`recovery.py` |
| `app.business` | 授权、业务规则、事务与幂等 | `policies.py`、`service.py`、`repository.py`、`idempotency.py`、`tools.py` |
| `app.storage` | SQLite 建表、实体、种子数据与 Checkpointer | `database.py`、`models.py`、`seed.py`、`persistence.py` |
| `app.observability` | 结构化 Trace 与脱敏 | `tracing.py` |
| `baselines` | A/B/C 三个对照版本的能力装配 | `react.py`、`rule_guarded.py`、`reliable.py` |
| `eval` | 评测执行、故障注入、Oracle、指标与报告 | `harness.py`、`oracle.py`、`metrics.py`、`reporter.py` |
| `tests` | 确定性单元与集成测试 | `test_*.py` |
| `scripts` | 数据库初始化与演示入口 | `init_db.py`、`demo.py` |

### 8.2 文件级职责

| 模块 | 职责 |
|---|---|
| `app/main.py` | FastAPI 生命周期、路由、错误响应 |
| `app/config.py` | 配置模型、数据库、超时、步数、确认 TTL、Trace |
| `app/schemas.py` | 工具参数/结果、API 请求/响应、确认契约 |
| `app/agent/graph.py` | 构建 LangGraph 图、节点连接和条件路由 |
| `app/agent/state.py` | 定义 `AgentState` 与每轮状态重置 |
| `app/agent/nodes.py` | 模型推理、预检、确认、执行、响应节点 |
| `app/agent/prompts.py` | 版本化系统提示词和工具使用规范 |
| `app/agent/model_client.py` | 真实模型适配与可复现的离线 Mock |
| `app/harness/runtime.py` | 会话启动/恢复、线程、预算、对外结果 |
| `app/harness/tool_executor.py` | 参数校验、身份注入、超时、错误归一化 |
| `app/harness/confirmation.py` | 待确认操作持久化、确认 ID、状态机 |
| `app/harness/recovery.py` | 重试判断、幂等查询、恢复协调 |
| `app/business/policies.py` | 纯函数业务规则与状态迁移 |
| `app/business/service.py` | 授权、事务、幂等、取消/退款/结算 |
| `app/business/repository.py` | 数据访问，所有查询按用户过滤 |
| `app/business/idempotency.py` | 请求指纹、同键重放、冲突检测 |
| `app/business/tools.py` | 六个工具的描述、Schema 与 Service 调用 |
| `app/storage/database.py` | SQLite 连接、建表、事务、约束 |
| `app/storage/models.py` | 数据实体与行映射 |
| `app/storage/seed.py` | 加载模拟数据、确定性时间偏移 |
| `app/storage/persistence.py` | LangGraph SQLite Checkpointer |
| `app/observability/tracing.py` | JSONL Trace 与敏感字段脱敏 |
| `baselines/` | A/B/C 能力配置与运行时装配 |
| `eval/` | 隔离 Fixture、用户模拟、故障注入、Oracle、指标、报告 |
| `tests/` | 业务安全、工作流、恢复和评测判定测试 |
| `scripts/` | 数据库初始化与演示入口 |

---

## 9. 快速开始

### 9.1 获取代码

```bash
git clone <repository-url> retail-agent
cd retail-agent
```

### 9.2 环境准备

项目要求 Python 3.11（`requires-python = ">=3.11,<3.12"`）：

```bash
conda create -n retail-agent python=3.11.3 pip -y
conda activate retail-agent
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

可编辑安装后，`app`、`baselines`、`eval` 三个包以及 `retail-eval` 命令都可用。

### 9.3 配置

`.env` 用来保存本机配置和 API Key，已被 `.gitignore` 忽略，因此克隆仓库后需要自行创建。当前仓库尚未提供 `.env.example`。

在项目根目录（与 `pyproject.toml` 同级）新建名为 `.env` 的文件，填写以下内容：

```dotenv
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

将 `your-api-key` 替换为你自己的 API Key。如果使用其他 OpenAI 兼容服务，同时将 `LLM_BASE_URL` 和 `LLM_MODEL` 改为该服务提供的地址和模型名称。Windows 下注意文件名应为 `.env`，不要保存成 `.env.txt`。

当前模型客户端通过 `python-dotenv` 加载项目根目录的 `.env`，读取上述三项配置；已存在的系统环境变量优先于 `.env` 中的同名值。调用真实模型前需要完成配置，否则可能因缺少变量或认证失败而报错。

下面是项目规划的完整配置清单；除上述三项外，其余配置尚未接入当前代码，填写后暂不会生效：

| 变量 | 示例 | 说明 |
|---|---|---|
| `APP_ENV` | `development` | 运行环境 |
| `LLM_MODE` | `auto` | `auto/mock/openai`，`mock` 为离线确定性模型 |
| `LLM_MODEL` | `deepseek-chat` | 模型名称 |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容地址 |
| `LLM_API_KEY` | `sk-...` | 本地密钥，不提交 |
| `LLM_TEMPERATURE` | `0.0` | 实验温度 |
| `LLM_MAX_TOKENS` | `1024` | 最大输出 |
| `DATABASE_PATH` | `./data/retail.db` | 业务数据库 |
| `CHECKPOINT_DB_PATH` | `./data/checkpoints.db` | 工作流 Checkpointer |
| `AGENT_MAX_STEPS` | `8` | 最大推理步数 |
| `TOOL_TIMEOUT_SECONDS` | `5` | 单工具超时 |
| `MAX_TOOL_RETRIES` | `1` | 安全重试次数 |
| `CONFIRMATION_TTL_SECONDS` | `300` | 确认有效期 |
| `TRACE_ENABLED` | `true` | 是否启用 Trace |
| `TRACE_PATH` | `./data/traces.jsonl` | Trace 输出 |

可以在项目根目录运行以下命令检查忽略规则：

```bash
git check-ignore -v .env
```

预期输出包含 `.gitignore` 中的 `.env` 规则，表示本地配置不会出现在普通的待提交列表中。不要使用 `git add -f .env` 强制提交密钥；后续提供 `.env.example` 时，其中只放配置名称和占位符。

### 9.4 初始化数据库

```bash
python scripts/init_db.py --reset
```

会生成两个独立文件：

- `data/retail.db`：模拟用户、订单、物流、退款；
- `data/checkpoints.db`：LangGraph 工作流检查点。

`--reset` 会同时清理 Checkpointer，避免会话引用旧业务数据。

### 9.5 启动服务

```bash
uvicorn app.main:app --reload --port 8000
```

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/chat` | 发送用户消息，返回回复或待确认操作 |
| `POST` | `/confirm` | 确认或拒绝待确认操作 |
| `GET` | `/health` | 返回服务、模型模式、数据库与版本信息 |

### 9.6 运行测试

```bash
pytest
```

只跑快速用例（跳过慢速、集成与联网测试）：

```bash
pytest -m "not slow and not integration and not network"
```

安全核心逻辑的测试不依赖真实模型，使用 Mock 模型与固定 Clock，可完全离线运行。

### 9.7 运行评测

开发集：

```bash
python -m eval.run_eval --cases dev --baseline A,B,C --runs 1 --output reports/runs/dev
```

冻结测试集：

```bash
python -m eval.run_eval --cases test --baseline A,B,C --runs 1 --publish
```

产物目录：

```text
reports/runs/<timestamp>/
├── results.jsonl          # 每个 Case 的执行结果
├── metrics.json           # 汇总指标
├── baseline_comparison.md # A/B/C 对比
└── bad_cases.md           # 失败案例与根因
```

等价的入口是 `retail-eval` 命令。

### 9.8 运行演示脚本

```bash
python scripts/demo.py
```

三条演示链路：

1. 订单与物流查询；
2. 取消订单、用户确认、取消退款流水；
3. 退款申请、响应丢失、幂等恢复、内部结算。

---

## 10. 使用说明

本项目提供的是 HTTP 服务而非可导入的库，因此下面给的是最小调用示例。

### 10.1 最小调用

```python
import httpx
client = httpx.Client(base_url="http://localhost:8000")
resp = client.post("/chat", json={"session_id": "sess-demo-1", "message": "我要取消 ORD-1002"})
print(resp.json()["reply"], resp.json().get("pending_action"))
```

身份不由请求体携带：`session_id` 在服务端映射到具体用户，客户端无法通过字段伪造 `user_id`。

### 10.2 一轮需要确认的对话

查询类请求直接返回结果；写操作先返回待确认操作，用户确认后才落库。

```text
› 我要取消 ORD-1002

  ORD-1002 已支付、尚未发货，在 24 小时取消窗口内。
  取消后将生成一笔 ¥199.00 的退款流水，订单变为 canceled。
  确认执行？

  pending_action:
    confirmation_id: cfm_7f3a91c2e8b4        # 不可预测，兼作唯一凭证
    summary:         取消订单 ORD-1002（退款 ¥199.00）
    expires_at:      2026-10-26T12:55:00+08:00

› 确认

  已取消 ORD-1002，退款流水已生成（status: completed）。
```

拒绝、超时或过期都不会产生任何业务写入。

### 10.3 确认接口

```bash
curl -s http://localhost:8000/confirm \
  -H "Content-Type: application/json" \
  -d '{"session_id":"sess-demo-1","confirmation_id":"cfm_7f3a91c2e8b4","decision":"approve"}'
```

`decision` 取值 `approve` 或 `reject`。重复提交同一 `confirmation_id` 会返回第一次的结果，不会重复执行。

### 10.4 错误码

| 错误码 | 含义 |
|---|---|
| `ORDER_NOT_FOUND` | 订单不存在，或不属于当前用户（两者不区分） |
| `INVALID_STATE` | 订单状态不允许该操作 |
| `POLICY_DENIED` | 违反取消/退款规则或时间窗口 |
| `CONFIRMATION_REQUIRED` | 高风险操作缺少确认 |
| `CONFIRMATION_INVALID` | 确认过期、已消费或不存在 |
| `IDEMPOTENCY_CONFLICT` | 同一幂等键对应不同请求 |
| `TOOL_TIMEOUT` | 工具调用超时 |
| `INTERNAL_ERROR` | 未预期错误 |

---

## 11. 常见问题

**退款申请提交后，订单为什么还是 `shipped`？**

`create_refund` 只创建 `refund_requests.status = pending`，不改变订单状态。订单进入 `refunded` 只能由内部结算（`settle_refund`，非 Agent 工具）触发。因此申请处于 `pending` 时，Agent 只能回复“申请已提交”。

**取消已付款订单后，订单是 `canceled` 还是 `refunded`？**

`canceled`。取消是订单终止，退款是它的资金后果：同一个事务里会写入一条 `kind='cancellation'`、状态为完成态的退款流水，但订单不会被标成 `refunded`。

**查询别人的订单，为什么返回“订单不存在”而不是“无权访问”？**

跨用户访问统一返回 `ORDER_NOT_FOUND`。如果提示“无权访问”，就等于确认了该订单存在，把不属于该用户的信息泄露了出去。

**用户连续点两次确认，会退两笔款吗？**

不会。确认执行使用 `confirm:<confirmation_id>` 作为幂等键，作用域是 `(user_id, idempotency_key)`；第二次提交命中幂等记录，直接返回第一次的结果。

**写成功了但响应丢了，恢复时会重复执行吗？**

不会。业务写入与幂等记录在同一个 SQLite 事务中提交，恢复时按幂等键查结果并复用，而不是盲目重放。

**时间窗口在测试里为什么能稳定复现？**

所有窗口判定都走可注入的 Clock，评测使用固定测试时钟；业务代码里不直接读系统时间。

**支持部分退款、换货或维修吗？**

不支持。V1 只做全额退款，金额由服务端按实付金额计算，模型无法传入金额。

**跑测试和评测需要联网或 GPU 吗？**

不需要。安全核心逻辑使用 Mock 模型与固定 Clock，可完全离线运行；接入真实模型时只需一个 OpenAI 兼容的 HTTP 端点。

---

## 12. 贡献指南

**报告问题**

请附上复现步骤、期望行为、实际行为，以及 `reports/runs/<timestamp>/` 下对应的 Trace 片段（请先脱敏）。涉及业务规则的问题，请注明订单状态与时间窗口位置。

**提出功能请求**

说明使用场景与预期收益。涉及业务语义的变更（订单状态、时间窗口、退款规则），需要先同步第 3 节的业务模型与对应测试，再改代码。

**提交补丁**

- 保持 `pytest` 全绿，不为过测试而放宽断言。
- 业务规则改动必须同时修改 `app/business/policies.py` 与对应测试。
- 不得为了让评测通过而修改 `eval/test_cases.jsonl` 的期望值。
- 命名统一：订单终态用 `canceled`（单 l），不得写成 `cancelled`。
- 禁止在业务代码里直接取系统时间，时间必须来自注入的 Clock。
- 数据库写入只能经由 Service 层，不得在节点或工具里直接调用 Repository。

---

## 13. 版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| `0.1.0` | 2026-10-26 | 项目立项。锁定六态订单模型、退款申请与退款完成分离、确认与幂等协议、40 条评测集与 A/B/C 对照方案，建立工程骨架。 |

---

## 14. 文档与报告

| 文件 | 内容 |
|---|---|
| `reports/baseline_comparison.md` | 评测后生成的 A/B/C 指标对比与结论 |
| `reports/bad_cases.md` | 评测后生成的失败案例、Trace 与根因分析 |

---

## 15. 作者与联系

- 作者：李政研
- 问题反馈与功能请求：提交 Issue
- 版本与变更记录：见[版本历史](#13-版本历史)

---

## 16. 许可证

本仓库尚未附带 `LICENSE` 文件。在明确许可证之前，默认保留所有权利。

### 法律声明

本项目使用模拟用户、模拟订单、模拟物流与模拟退款数据，不接入真实支付、物流或生产系统，不处理真实个人信息。
