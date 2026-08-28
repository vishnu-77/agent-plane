"""Admin API: live revocation + policy hot-reload."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

TOKEN = "s3cret-admin"
H = {"X-Admin-Token": TOKEN}


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
