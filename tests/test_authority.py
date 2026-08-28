"""The task-authority edge (/v1/authorize) - capability != authority.

Uses the shipped config/leases.yaml demo data: `devops-agent` holds a lease for
task `fix-staging-checkout` scoped to `staging/*` (protected: `production/*`),
and `repo-agent` holds a lease for `remediate-stale-branches` scoped to one
repo with `branches/main` carved out and a `branch.delete` use limit.
"""
from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient

JWT_SECRET = "authority-secret"


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


def test_action_within_task_scope_allowed(client):
    token = _token(agent_id="devops-agent")
    r = _authorize(client, token, "fix-staging-checkout", "deployment.restart", "staging/checkout")
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "allow"
    assert body["reason"] == "ACTION_WITHIN_TASK_AUTHORITY"
    assert body["lease"] == "lease-fix-staging"
    assert body["evidence_id"].startswith("az_")


def test_resource_outside_task_scope_denied(client):
    # The doc's motivating example: capability present, credential valid, but
    # the task only authorised staging - production is out of scope entirely.
    token = _token(agent_id="devops-agent")
    r = _authorize(client, token, "fix-staging-checkout", "deployment.restart", "production/checkout")
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "RESOURCE_OUTSIDE_DELEGATED_SCOPE"


def test_protected_resource_denied_even_within_broader_scope(client):
    # branch.delete is in-scope for the repo generally, but main is carved out.
    token = _token(agent_id="repo-agent")
    r = _authorize(
        client, token, "remediate-stale-branches", "branch.delete",
        "github://acme/agent-plane-demo/branches/main",
    )
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "RESOURCE_PROTECTED"


def test_action_not_in_lease_denied(client):
    token = _token(agent_id="devops-agent")
    r = _authorize(client, token, "fix-staging-checkout", "deployment.delete", "staging/checkout")
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "ACTION_NOT_AUTHORIZED"


def test_no_lease_for_task_denied(client):
    token = _token(agent_id="devops-agent")
    r = _authorize(client, token, "some-other-task", "deployment.restart", "staging/checkout")
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "NO_ACTIVE_LEASE"


def test_use_limit_enforced_then_exceeded(client):
    token = _token(agent_id="repo-agent")
    branch = "github://acme/agent-plane-demo/branches/stale-{}"
    for i in range(5):
        r = _authorize(
            client, token, "remediate-stale-branches", "branch.delete", branch.format(i)
        )
        assert r.status_code == 200, r.json()

    r = _authorize(client, token, "remediate-stale-branches", "branch.delete", branch.format(5))
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "ACTION_LIMIT_EXCEEDED"


def test_expired_lease_denied(client, tmp_path, monkeypatch):
    leases_file = tmp_path / "leases.yaml"
    leases_file.write_text(
        "leases:\n"
        "  - metadata: { id: l1, task: t1 }\n"
        "    subject: { agent: a1 }\n"
        "    authority: { resources: ['*'], actions: ['x'] }\n"
        "    constraints: { expires_at: '2000-01-01T00:00:00Z' }\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("LEASES_FILE", str(leases_file))
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        r = _authorize(c, _token(agent_id="a1"), "t1", "x", "anything")
        assert r.status_code == 403
        assert r.json()["detail"]["reason"] == "LEASE_EXPIRED"
    get_settings.cache_clear()


def test_capability_manifest_gates_before_any_lease(client):
    # The agent's capability manifest (allowed_tools) doesn't cover "deployment"
    # at all - denied at the capability gate, independent of any lease.
    token = _token(agent_id="devops-agent", allowed_tools=["github"])
    r = _authorize(client, token, "fix-staging-checkout", "deployment.restart", "staging/checkout")
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "ACTION_OUTSIDE_CAPABILITY_MANIFEST"


def test_decision_is_audited_and_tamper_evident(client):
    token = _token(agent_id="devops-agent")
    r = _authorize(client, token, "fix-staging-checkout", "deployment.restart", "staging/checkout")
    evidence_id = r.json()["evidence_id"]

    audit = client.get("/v1/audit", headers={"X-Admin-Token": "test-admin"}).json()["events"]
    events = [e for e in audit if e["decision_id"] == evidence_id]
    assert events and events[0]["model_requested"] == "authorize:deployment.restart"
    assert events[0]["decision"] == "allow"
    assert events[0]["signature"]


def test_admin_can_issue_and_read_back_a_lease(client):
    r = client.post(
        "/v1/leases",
        headers={"X-Admin-Token": "test-admin"},
        json={
            "id": "lease-runtime-1", "task": "t1", "agent": "a1",
            "resources": ["res/*"], "actions": ["read"],
        },
    )
    assert r.status_code == 200
    assert r.json()["issued"] is True

    r = client.get("/v1/leases/lease-runtime-1", headers={"X-Admin-Token": "test-admin"})
    assert r.status_code == 200
    assert r.json()["subject"] == "a1"

    # And it's immediately enforceable.
    r = _authorize(client, _token(agent_id="a1"), "t1", "read", "res/thing")
    assert r.status_code == 200


def test_lease_issuance_requires_admin_token(client):
    r = client.post("/v1/leases", json={"id": "x", "task": "t", "agent": "a"})
    assert r.status_code in (401, 404)
