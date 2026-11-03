"""测试默认关闭 Trace，避免向 data/traces.jsonl 写入临时事件。"""

import pytest


@pytest.fixture(autouse=True)
def disable_trace_by_default(monkeypatch):
    monkeypatch.setenv("TRACE_ENABLED", "false")