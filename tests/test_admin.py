"""Admin API: live revocation + policy hot-reload."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

TOKEN = "s3cret-admin"
H = {"X-Admin-Token": TOKEN}


@pytest.mark.parametrize("path", ["/admin/gateway", "/admin/leases", "/admin/policies"])
def test_console_inventory_requires_operator_access(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.get(path, headers=H).status_code == 200


def test_gateway_inventory_excludes_secrets_and_knowledge_content(client):
    from agent_plane.routing.tools import ToolRegistry, ToolSpec

    client.app.state.settings.openai_api_key = "private-provider-key"
    client.app.state.tools = ToolRegistry([
        ToolSpec(name="test-tool", type="http", url="https://private.example/token-in-url",
                 secret_env="PRIVATE_TOOL_SECRET")
    ])
    response = client.get("/admin/gateway", headers=H)
    data = response.json()
    assert data["models"][0]["credentials_configured"] is True
    assert data["tools"] == [{"name": "test-tool", "type": "http", "method": "POST"}]
    assert data["knowledge"] == [{"name": "kb", "type": "local"}]
    for private in ("private-provider-key", "PRIVATE_TOOL_SECRET", "token-in-url", "severance", TOKEN):
        assert private not in response.text


def test_lease_inventory_reports_status_and_actual_uses(client):
    from datetime import UTC, datetime, timedelta

    from agent_plane.authority.lease import AuthorityLease

    store = client.app.state.leases
    for lease_id in ("expired-test", "revoked-test", "active-test"):
        store.add(AuthorityLease(id=lease_id, task="task", subject="agent", actions=["read"],
                                 resources=["test/*"]))
    store.add(store.get("expired-test").model_copy(update={
        "expires_at": datetime.now(UTC) - timedelta(hours=1),
    }))
    store.revoke("revoked-test")
    store.try_consume("active-test", "read", 5)
    leases = {item["id"]: item for item in client.get("/admin/leases", headers=H).json()["items"]}
    assert leases["expired-test"]["status"] == "expired"
    assert leases["revoked-test"]["status"] == "revoked"
    assert leases["active-test"]["status"] == "active"
    assert leases["active-test"]["uses"] == {"read": 1}


def test_policy_inventory_includes_conditions(client):
    data = client.get("/admin/policies", headers=H).json()
    sensitive = next(p for p in data["policies"] if p["name"] == "sensitive-tool-approval")
    assert sensitive["decision"]["action"] == "approval_required"
    assert "wire_transfer" in sensitive["match"]["tools"]


def test_production_reload_preserves_active_bundle_on_empty_candidate(client, monkeypatch):
    from agent_plane.policy.loader import PolicyBundle

    client.app.state.settings.environment = "production"
    before = client.app.state.engine.bundle.version
    monkeypatch.setattr("agent_plane.gateway.admin.load_bundle", lambda _: PolicyBundle([]))
    assert client.post("/admin/policies/reload", headers=H).status_code == 400
    assert client.app.state.engine.bundle.version == before


def test_issued_lease_is_audited(client):
    response = client.post("/v1/leases", headers=H, json={
        "id": "console-issued", "task": "inspect", "agent": "inspector",
        "actions": ["read"], "resources": ["staging/*"],
    })
    assert response.status_code == 200
    events = client.get("/v1/audit", headers=H).json()["events"]
    assert any(e["reason"] == "LEASE_ISSUED" and e["signature"] for e in events)


def _build(tmp_path, monkeypatch, admin_token: str | None):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    if admin_token is None:
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    else:
        monkeypatch.setenv("ADMIN_TOKEN", admin_token)

    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    return create_app()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    app = _build(tmp_path, monkeypatch, TOKEN)
    with TestClient(app) as c:
        yield c
    from agent_plane.config import get_settings

    get_settings.cache_clear()


def test_disabled_without_admin_token(tmp_path, monkeypatch):
    app = _build(tmp_path, monkeypatch, None)
    with TestClient(app) as c:
        assert c.get("/admin/revocations", headers=H).status_code == 404
    from agent_plane.config import get_settings

    get_settings.cache_clear()


def test_wrong_token_unauthorized(client):
    assert client.get("/admin/revocations", headers={"X-Admin-Token": "nope"}).status_code == 401
    assert client.get("/admin/revocations").status_code == 401


def test_revoke_add_list_remove(client):
    r = client.post("/admin/revocations", headers=H, json={"jti": "tok-1"})
    assert r.status_code == 200 and "tok-1" in r.json()["revoked"]
    assert "tok-1" in client.get("/admin/revocations", headers=H).json()["revoked"]
    client.delete("/admin/revocations/tok-1", headers=H)
    assert "tok-1" not in client.get("/admin/revocations", headers=H).json()["revoked"]


def test_revoke_requires_jti(client):
    assert client.post("/admin/revocations", headers=H, json={}).status_code == 400


def test_audit_endpoint_is_admin_only(client):
    # Audit is operator evidence - must not be readable without the admin token.
    assert client.get("/v1/audit").status_code == 401
    assert client.get("/v1/audit", headers=H).status_code == 200


def test_policy_hot_reload(client):
    before = client.get("/admin/policies", headers=H).json()
    assert before["policy_version"].startswith("bundle-")
    r = client.post("/admin/policies/reload", headers=H)
    assert r.status_code == 200 and r.json()["reloaded"] is True
    assert any("pii-redaction" in name for name in r.json()["rules"])


def test_admin_mutations_are_audited(client):
    # Revoking, un-revoking, and reloading policy are security-sensitive - they
    # must land in the same signed audit chain as every other edge, not be a
    # blind spot for an attacker holding the admin token.
    client.post("/admin/revocations", headers=H, json={"jti": "tok-audit"})
    client.delete("/admin/revocations/tok-audit", headers=H)
    client.post("/admin/policies/reload", headers=H)

    audit = client.get("/v1/audit", headers=H).json()["events"]
    kinds = {e["model_requested"] for e in audit}
    assert "admin:revoke_add" in kinds
    assert "admin:revoke_remove" in kinds
    assert "admin:policy_reload" in kinds
    add_event = next(e for e in audit if e["model_requested"] == "admin:revoke_add")
    assert "tok-audit" in add_event["reason"]
    assert add_event["signature"]  # same tamper-evident chain as every other edge
