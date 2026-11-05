"""在确认执行提交之后丢弃响应，模拟调用方断线。

只有 after_commit 一个故障点：``decide_confirmation`` 在同一个事务里完成业务写入
和确认消费，返回时数据库状态已经提交。此刻丢弃返回值，等价于
「订单已取消、退款已生成，但用户没收到回复」。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.observability.tracing import emit_trace, new_run_id

FAULT_POINTS = frozenset({"after_commit"})


class ResponseLost(RuntimeError):
    """响应在返回途中丢失；业务写入已经提交，不因此回滚。"""


@dataclass(frozen=True)
class FaultConfig:
    point: str = "after_commit"


def parse_fault(data: Any) -> FaultConfig | None:
    """把用例里的 fault 字段转成配置；缺省表示不注入。"""
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("fault 必须是对象")
    point = data.get("point", "after_commit")
    if point not in FAULT_POINTS:
        raise ValueError(f"fault.point 只支持 {sorted(FAULT_POINTS)}")
    return FaultConfig(point=point)


class FaultInjector:
    """只在第一次提交后丢弃响应，之后恢复正常返回，避免故障无限重演。"""

    def __init__(self, config: FaultConfig):
        if config.point not in FAULT_POINTS:
            raise ValueError(f"未知故障点: {config.point}")
        self.config = config
        self.fired = 0

    @property
    def triggered(self) -> bool:
        return self.fired > 0

    def after_commit(
        self,
        *,
        tool: str,
        confirmation_id: str,
        call: Callable[[], dict],
    ) -> dict:
        """执行 call()，提交完成后丢弃它的返回值。

        call() 自身抛异常时不触发故障：故障只发生在「写入已成功」之后。
        """
        result = call()
        if self.fired:
            return result

        self.fired += 1
        emit_trace(
            "fault.injected",
            run_id=new_run_id(),
            point=self.config.point,
            tool=tool,
            confirmation_id=confirmation_id,
        )
        raise ResponseLost(f"响应丢失：{tool} 已提交，调用方未收到结果")
