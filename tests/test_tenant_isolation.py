"""Two tenants must never share or collide leases, even with the same agent_id
and task string (the LeaseStore key was subject+task only, with no tenant
field, before this was fixed - see agent_plane/authority/store.py)."""
from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient

JWT_SECRET = "tenant-isolation-secret"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _token(**claims) -> str:
    return jwt.encode({"sub": "u1", "tenant": "default", **claims}, JWT_SECRET, algorithm="HS256")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _authorize(client, token, task, action, resource):
    return client.post(
        "/v1/authorize",
        headers=_auth(token),
        json={"task": task, "action": action, "resource": resource},
    )


def test_same_agent_id_and_task_do_not_cross_tenants(client):
    # tenant-a issues a lease for "shared-agent" on task "cleanup".
    r = client.post(
        "/v1/leases",
        headers={"X-Admin-Token": "test-admin"},
        json={
            "id": "lease-tenant-a", "task": "cleanup", "agent": "shared-agent",
            "tenant": "tenant-a", "resources": ["*"], "actions": ["read"],
        },
    )
    assert r.status_code == 200

    # tenant-a's own actor, same agent_id/task -> allowed.
    r = _authorize(client, _token(agent_id="shared-agent", tenant="tenant-a"), "cleanup", "read", "x")
    assert r.status_code == 200

    # tenant-b using the *same* agent_id and *same* task string must not
    # inherit tenant-a's lease.
    r = _authorize(client, _token(agent_id="shared-agent", tenant="tenant-b"), "cleanup", "read", "x")
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "NO_ACTIVE_LEASE"


def test_delegated_child_lease_inherits_parents_tenant_not_the_caller_claim(client):
    client.post(
        "/v1/leases",
        headers={"X-Admin-Token": "test-admin"},
        json={
            "id": "lease-parent-b", "task": "t1", "agent": "parent-agent",
            "tenant": "tenant-b", "resources": ["*"], "actions": ["read"],
        },
    )
    # Delegating actor's own JWT claims a different tenant than the lease -
    # the child must still carry the parent lease's tenant, not the caller's.
    r = client.post(
        "/v1/leases/lease-parent-b/delegate",
        headers=_auth(_token(agent_id="parent-agent", tenant="tenant-a")),
        json={"agent": "child-agent"},
    )
    assert r.status_code == 200
    assert r.json()["lease"]["tenant"] == "tenant-b"

    # Child is usable under tenant-b, not tenant-a.
    r = _authorize(client, _token(agent_id="child-agent", tenant="tenant-b"), "t1", "read", "x")
    assert r.status_code == 200
    r = _authorize(client, _token(agent_id="child-agent", tenant="tenant-a"), "t1", "read", "x")
    assert r.status_code == 403
