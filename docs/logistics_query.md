# 查询本人订单物流

对应 README 第 4 节的 `get_logistics`：用户提供订单号，系统校验订单归属，
返回承运商、运单号和按时间从早到晚排列的轨迹。当前只查询本地模拟物流，不对接真实快递公司。

## 运行与体验

在项目根目录运行：

```powershell
conda run -n retail-agent python -m scripts.init_db
conda run --no-capture-output -n retail-agent python -m scripts.demo_chat
```

如出现历史对话菜单，选择 `0` 开始新对话，然后分别询问：

```text
ORD-2002 的快递到哪了？
ORD-1001 有物流吗？
查一下 ORD-1002 的物流
```

按默认模拟数据，预期分别为：

1. 返回模拟速递、运单号 `MOCK2002001`，以及揽收、到达分拨中心、正在派送三条轨迹。
2. 订单属于当前模拟用户，但暂无物流记录。
3. 当前模拟用户 `USER-001` 无法访问该订单，返回未找到可访问订单；不泄露其他用户的物流。

承运商、运单号和轨迹均为模拟数据。轨迹时间以带时区的 UTC ISO 文本存储；
模型不可根据日期自行推断已签收或预计送达时间。
如果已有订单或物流被手动修改，以当前数据库为准；初始化不会重置这些记录。
已有待确认操作恢复后仍等待确认/拒绝；想体验物流查询时可选择新对话。

## 新增代码的作用

```text
聊天请求 → 模型提出 get_logistics(order_id)
         → ToolExecutor 校验参数并注入当前用户
         → Tools 转发到 Service
         → Service 检查订单归属
         → Repository 查询 SQLite 物流和轨迹
         → 工具结果返回模型，模型组织回复
```

| 文件 | 职责 |
| --- | --- |
| `app/storage/database.py` | 增量创建 `shipments` 和 `logistics_events`；组合外键约束物流与订单归属、轨迹与物流归属一致。 |
| `app/storage/seed.py` | 为模拟订单 ORD-2002 加载一份物流和三条轨迹；同一事务写入，已有物流不覆盖或追加。 |
| `scripts/init_db.py` | 编排迁移、订单/退款和物流模拟数据加载。 |
| `app/business/repository.py` | 在一个读取事务中查询物流及轨迹，按用户过滤并按轨迹时间排序。 |
| `app/business/service.py` | 区分订单不可访问与本人订单暂无物流记录。 |
| `app/business/tools.py` | 声明模型工具契约，参数只有 order_id，并转发 Service。 |
| `app/harness/tool_executor.py` | 拒绝非法参数和额外身份字段，从可信上下文注入 user_id。 |
| `app/agent/prompts.py` | 指导模型选择物流工具、追问订单号、说明模拟来源并按真实查询结果回答。 |
| `tests/test_logistics.py` | 验证旧库迁移、数据保留、重复加载、授权、约束、边界、零写入及聊天入口。 |

返回示例：

```json
{
  "ok": true,
  "order_id": "ORD-2002",
  "logistics": {
    "carrier": "模拟速递",
    "tracking_number": "MOCK2002001",
    "events": [
      {"occurred_at": "2026-10-26T03:00:00+00:00", "description": "模拟：快递已揽收"},
      {"occurred_at": "2026-10-27T04:00:00+00:00", "description": "模拟：到达分拨中心"},
      {"occurred_at": "2026-10-28T02:00:00+00:00", "description": "模拟：快递正在派送"}
    ]
  }
}
```

本人订单暂无物流时，`ok=true` 且 `logistics=null`；存在运单但没有轨迹时，`events=[]`。
订单不存在或不属于当前用户，统一返回 `{"ok": false, "error": "ORDER_NOT_FOUND"}`。

## 数据与测试验收

```powershell
conda run -n retail-agent python -m pytest tests/test_logistics.py
conda run -n retail-agent python -m pytest
```

物流测试共 24 个，全量测试为 121 passed, 4 subtests passed。
测试仅替换联网模型响应，迁移、Service、Repository、工具执行与 SQLite 均真实执行。
离线测试证明执行链路，不代表真实模型已通过所有物流意图和回答质量验收。

重复运行初始化不重复追加模拟轨迹，也不覆盖已有订单、退款、确认、聊天历史和物流修改。
物流工具自身不写数据库；从聊天入口查询后保存聊天历史是既有行为。

本轮范围是一个订单一份物流信息、多条轨迹。尚未实现多包裹、真实物流同步或订阅通知。
