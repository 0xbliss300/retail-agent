"""单轮对话的执行预算：限制工具调用总数与总耗时。

``max_steps`` 只限制模型循环次数，管不住一次返回多个 tool_calls 的模型。
预算超限时不抛出异常、不静默截断，而是给每个未执行的工具补一条结构化结果，
再返回确定性说明——否则消息序列会被破坏，下一轮请求直接 400。
"""

from collections.abc import Callable
from time import monotonic

MAX_TOOL_CALLS_PER_RUN = 8
MAX_RUN_DURATION_SECONDS = 30.0

TOOL_BUDGET_ANSWER = (
    "本轮执行的操作数量已达到上限，剩余操作没有执行。"
    "已执行的变更不会回滚，请先查询订单或退款状态，确认后再继续。"
)
TIME_BUDGET_ANSWER = (
    "本轮处理时间已超过上限，未完成的操作没有执行。"
    "涉及取消或退款时，请先查询订单和退款状态，不要重复提交。"
)


class ExecutionBudget:
    """按工具调用次数与单调时钟累计耗时限制一轮执行。"""

    def __init__(
        self,
        *,
        max_tool_calls: int = MAX_TOOL_CALLS_PER_RUN,
        max_seconds: float = MAX_RUN_DURATION_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls 至少为 1")
        if max_seconds <= 0:
            raise ValueError("max_seconds 必须大于 0")
        self.max_tool_calls = max_tool_calls
        self.max_seconds = max_seconds
        self._clock = clock
        self._started = clock()
        self._tool_calls = 0

    @property
    def tool_calls(self) -> int:
        return self._tool_calls

    def has_tool_quota(self) -> bool:
        """是否还有剩余工具调用额度；检查通过后再调用 consume_tool。"""
        return self._tool_calls < self.max_tool_calls

    def consume_tool(self) -> None:
        self._tool_calls += 1

    def expired(self) -> bool:
        return self._clock() - self._started >= self.max_seconds

    def snapshot(self) -> dict:
        return {
            "tool_calls": self._tool_calls,
            "max_tool_calls": self.max_tool_calls,
            "elapsed_ms": round((self._clock() - self._started) * 1000, 2),
            "max_seconds": self.max_seconds,
        }
