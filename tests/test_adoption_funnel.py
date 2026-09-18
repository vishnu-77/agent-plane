"""Phase 32: adoption funnel, computed from state already authoritative
elsewhere (accounts, registry, contracts) - not a separate event log."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("JWT_SECRET", "accounts-secret-0123456789")
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")
    from agent_plane.config import get_settings
    get_settings.cache_clear()
    from agent_plane.main import create_app
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _signup_project_key(client):
    r = client.post("/v1/auth/signup", json={"email": "dev@example.com", "password": "correct-horse-battery", "name": "Dev"})
    assert r.status_code == 200, r.text
    project = client.post("/v1/projects", json={"name": "proj", "mode": "observe"}).json()["project"]
    key = client.post("/v1/api-keys", json={"project": project["id"], "name": "k", "environment": "live"}).json()
    return project, key["secret"]


def test_funnel_starts_with_nothing_reached(client):
    project, _ = _signup_project_key(client)
    r = client.get("/v1/adoption-funnel", params={"tenant": project["id"]}, headers={"X-Admin-Token": "test-admin"})
    assert r.status_code == 200
    funnel = {s["step"]: s["reached"] for s in r.json()["funnel"]}
    assert funnel == {"connected": False, "first_observed_event": False, "first_agent_discovered": False,
                      "contract_generated": False, "contract_accepted": False, "enforce_enabled": False}


def test_funnel_advances_through_connect_and_first_event(client):
    project, secret = _signup_project_key(client)
    client.post("/v1/auth/exchange", headers={"Authorization": f"Bearer {secret}"}, json={"integration": "claude-code"})
    client.post("/v1/events/action", headers={"Authorization": f"Bearer {secret}"},
               json={"integration": "claude-code", "agent": "claude-code", "task": "t1", "tool": "Read",
                     "arguments": {"file_path": "a.ts"}})
    r = client.get("/v1/adoption-funnel", params={"tenant": project["id"]}, headers={"X-Admin-Token": "test-admin"})
    funnel = {s["step"]: s["reached"] for s in r.json()["funnel"]}
    assert funnel["connected"] is True
    assert funnel["first_observed_event"] is True
    assert funnel["first_agent_discovered"] is True
    assert funnel["contract_generated"] is False


def test_funnel_advances_through_contract_and_enforce(client):
    project, _ = _signup_project_key(client)
    client.post("/v1/contracts", json={"contract_id": "c1", "agent": "claude-code", "tenant": project["id"],
                                       "allow": ["repo.read"], "approved_by": "platform"},
               headers={"X-Admin-Token": "test-admin"})
    client.patch(f"/v1/projects/{project['id']}", json={"mode": "enforce"})
    r = client.get("/v1/adoption-funnel", params={"tenant": project["id"]}, headers={"X-Admin-Token": "test-admin"})
    funnel = {s["step"]: s["reached"] for s in r.json()["funnel"]}
    assert funnel["contract_generated"] is True
    assert funnel["contract_accepted"] is True
    assert funnel["enforce_enabled"] is True
