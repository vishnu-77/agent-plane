"""Usage metering: store aggregation + /v1/usage endpoint."""
from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient

from agent_plane.usage.store import SqlUsageStore

JWT_SECRET = "usage-secret"


def test_usage_store_aggregates(tmp_path):
    store = SqlUsageStore(f"sqlite:///{tmp_path / 'u.db'}")
    store.record({"tenant": "acme", "user_id": "u1", "edge": "model", "resource": "gpt-4.1", "units": 100, "calls": 1})
    store.record({"tenant": "acme", "user_id": "u1", "edge": "model", "resource": "gpt-4.1", "units": 50, "calls": 1})
    store.record({"tenant": "other", "user_id": "x", "edge": "tool", "resource": "search_kb", "units": 1, "calls": 1})

    acme = store.summary("acme")
    assert len(acme) == 1
    assert acme[0] == {"edge": "model", "resource": "gpt-4.1", "calls": 2, "units": 150}
    assert store.summary("nobody") == []


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _auth(**claims):
    token = jwt.encode({"sub": "u1", "tenant": "default", **claims}, JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def test_tool_call_is_metered_and_reported(client):
    client.post("/v1/tools/invoke", headers=_auth(), json={"tool": "search_kb", "arguments": {"q": "x"}})
    usage = client.get("/v1/usage", headers=_auth()).json()
    assert usage["tenant"] == "default"
    tool_items = [i for i in usage["items"] if i["resource"] == "search_kb"]
    assert tool_items and tool_items[0]["calls"] == 1
    # config/pricing.yaml is present -> estimated cost attached (metering, not billing).
    assert "estimated_cost" in usage["totals"]
    assert usage["totals"]["currency"] == "USD"


def test_usage_is_tenant_isolated(client):
    client.post("/v1/tools/invoke", headers=_auth(), json={"tool": "echo", "arguments": {}})
    other = client.get("/v1/usage", headers=_auth(tenant="other-co")).json()
    assert other["items"] == []
