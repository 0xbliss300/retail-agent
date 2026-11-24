"""测试默认关闭 Trace，避免向 data/traces.jsonl 写入临时事件。"""

import pytest

from app.storage.seed import seed_demo_accounts


@pytest.fixture(autouse=True)
def disable_trace_by_default(monkeypatch):
    monkeypatch.setenv("TRACE_ENABLED", "false")


@pytest.fixture
def login_demo():
    def login(client, *, second=False):
        seed_demo_accounts()
        email, password = (
            ("demo2@retailops.local", "demo5678") if second else ("demo@retailops.local", "demo1234")
        )
        response = client.post("/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200
        return response

    return login
