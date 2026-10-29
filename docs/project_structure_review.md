# README 第 7 节职责核对（2026-10-28）

结论：当前是部分可运行的开发阶段，尚未满足完整目录描述。
以下根据实际文件内容核对；空文件仅表示目录骨架，不表示功能已实现。
README 保留目标设计，本文件记录本次检查后的实际状态。

## 已有代码

| 文件 | 实际状态与差距 |
| --- | --- |
| `app/agent/model_client.py` | 有真实 OpenAI 兼容请求适配；没有离线 Mock 模式，直接读取环境变量，超时和重试硬编码。测试中的模型替身不等于这里已有 Mock 适配。 |
| `app/agent/prompts.py` | 有系统提示词与订单、政策使用说明；缺少 Prompt 版本，目前明确只开放两个只读工具。 |
| `app/harness/runtime.py` | 有消息历史、模型→工具→模型循环与推理轮数限制；没有线程持久化、恢复、Trace 等完整执行控制。 |
| `app/harness/tool_executor.py` | 有两个工具的参数检查和身份注入；没有超时与执行异常归一化，尚未使用 Pydantic 契约。 |
| `app/business/tools.py` | **本次修正**：保留工具描述与 Service 转发，移出直接数据访问、政策正文和内嵌演示；仍只有 `get_order`、`get_policy`，未满足六个工具的目标。 |
| `app/business/service.py` | **本次修正**：承接订单与政策查询；已有独立的只读退款资格预检。订单查询缺少最近退款摘要；未实现事务写入、确认复验、幂等、取消、创建退款和内部结算。 |
| `app/business/repository.py` | 已有按用户过滤的订单和处理中退款查询；没有物流、确认、幂等、日志等其他数据访问。 |
| `app/business/policies.py` | **本次修正**：集中维护通用政策正文；已有退款状态与时间窗口纯函数。仍缺少取消规则、六态迁移校验、Policy 版本，政策正文尚非从结构化规则自动生成。 |
| `app/storage/database.py` | 有连接、订单时间字段增量迁移、退款表与部分约束、初始化事务；仍在建表时插入两条模拟订单，越过 seed 职责。尚无用户、物流、确认、幂等、操作日志表，订单六态约束等也未补齐。 |
| `app/storage/seed.py` | 有模拟订单和 pending 退款的可重复加载；使用固定日期，没有确定性时间偏移，也没有用户、物流模拟数据。 |
| `scripts/init_db.py` | 有建表和种子加载编排；没有重置选项、独立 Checkpointer 初始化。不要按 README 假设已支持 `--reset`。 |
| `scripts/demo_chat.py` | 实际可用的交互入口，调用真实模型，使用固定模拟身份；未列入第 7 节，不能代替目标中的三条售后演示。 |
| `tests/test_runtime.py` | 有离线模型替身、临时数据库、对话历史、身份隔离、非法参数与步数上限验证；本次同步了提示词参数和订单时间字段。 |
| `tests/test_tools.py` | 本次补充两个只读工具的正常、拒绝路径与数据库零写入验证；尚未覆盖六个目标工具。 |
| `pyproject.toml` | 已有依赖、包发现、pytest/Ruff/mypy 配置；`retail-eval` 指向的函数仍未实现。配置存在不代表所有检查均已通过。 |
| `.gitignore` | 已排除 `.env` 与 Python 缓存；缺少数据库、Trace、评测临时产物、虚拟环境等排除项。 |

## 空骨架与缺失文件

以下文件为空，尚不具备第 7 节所述职责：

- 应用入口与契约：`app/main.py`、`app/config.py`、`app/schemas.py`。
- 工作流：`app/agent/graph.py`、`nodes.py`、`state.py`。
- 可靠执行：`app/harness/confirmation.py`、`recovery.py`，`app/business/idempotency.py`。
- 持久化实体与观测：`app/storage/models.py`、`persistence.py`，`app/observability/tracing.py`。
- 对照版本：`baselines/react.py`、`rule_guarded.py`、`reliable.py`。
- 评测：`eval/` 下的业务 Python 模块，以及 `dev_cases.jsonl`、`test_cases.jsonl`。
- 测试：`tests/conftest.py`、`test_policy.py`、`test_authorization.py`、`test_idempotency.py`、`test_confirmation.py`、`test_recovery.py`、`test_graph.py`、`test_oracle.py`。
- 数据与报告：`data/fixtures/seed_orders.json`、`reports/baseline_comparison.md`、`reports/bad_cases.md`。
- 目标演示入口：`scripts/demo.py`。

第 7 节列出的 `tests/test_storage.py`、`.env.example` 缺失。
各包的 `__init__.py` 为空本身没有问题：它们可仅用于标记 Python 包。

## 本次最小重构

输入：可信用户 ID、订单 ID，或政策主题。
输出：沿用原有结果字典、错误码与工具函数签名。

```text
Runtime → ToolExecutor → Tools → Service → Repository → SQLite
                                      └→ Policies
```

Tools 描述模型可以调用什么；Service 提供业务入口并决定如何组合查询、规则与结果；
Repository 执行按用户过滤的 SQL；Policies 保存规则和政策。
Tools 不再直接访问 Repository。原先 `tools.py` 中的内嵌演示由离线测试覆盖。
数据库结构、本地业务数据与现有退款资格预检接口未改动。

验收命令（在项目根目录执行）：

```powershell
conda run -n retail-agent python -m pytest
```

本次验证结果：`10 passed, 4 subtests passed`。新增只读工具测试使用独立临时数据库，
并比较调用前后的完整 SQL dump；真实模型没有参与验证。
这证明当前只读链路的相关行为，不代表目标 API、写操作或评测系统已经完成。

下一条建议链路：分离建表与模拟数据加载，将 `database.py` 中的模拟订单移到 `seed.py`，
保留已有数据，并验证重复初始化和迁移。按协作约束，本次不继续实现。

请运行上述命令，并用自己的话说明：为什么订单查询应经过 Service，
而不是让 Tools 直接调用 Repository？收到运行结果与理解反馈后再进入下一条链路。
