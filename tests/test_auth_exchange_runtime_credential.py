"""PR-5, app-level: /v1/auth/exchange stays backward compatible, and a minted
runtime credential enforces the same scope as the raw Project API Key it
came from."""
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient


def _keypair() -> tuple[str, str]:
    key = ed25519.Ed25519PrivateKey.generate()
    priv = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv, pub


@pytest.fixture()
def client_no_signing_key(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("JWT_SECRET", "accounts-secret-0123456789")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("DELEGATION_SIGNING_KEY", raising=False)
    from agent_plane.config import get_settings
    get_settings.cache_clear()
    from agent_plane.main import create_app
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


@pytest.fixture()
def client_with_signing_key(tmp_path, monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("JWT_SECRET", "accounts-secret-0123456789")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("DELEGATION_SIGNING_KEY", priv)
    monkeypatch.setenv("DELEGATION_PUBLIC_KEY", pub)
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


def test_exchange_omits_runtime_credential_when_signing_key_unset(client_no_signing_key):
    _, secret = _signup_project_key(client_no_signing_key)
    r = client_no_signing_key.post("/v1/auth/exchange", headers={"Authorization": f"Bearer {secret}"},
                                   json={"integration": "claude-code"})
    assert r.status_code == 200
    assert "runtime_credential" not in r.json()


def test_exchange_includes_runtime_credential_when_signing_key_set(client_with_signing_key):
    _, secret = _signup_project_key(client_with_signing_key)
    r = client_with_signing_key.post("/v1/auth/exchange", headers={"Authorization": f"Bearer {secret}"},
                                     json={"integration": "claude-code"})
    assert r.status_code == 200
    body = r.json()
    assert "token" in body["runtime_credential"] and "expires_at" in body["runtime_credential"]


def test_runtime_credential_has_same_scope_as_raw_key(client_with_signing_key):
    """Scope parity: an action allowed/blocked with the raw Project API Key
    is allowed/blocked identically with the runtime credential minted from it."""
    _, secret = _signup_project_key(client_with_signing_key)
    exchange = client_with_signing_key.post("/v1/auth/exchange", headers={"Authorization": f"Bearer {secret}"},
                                            json={"integration": "claude-code"}).json()
    token = exchange["runtime_credential"]["token"]

    raw_report = client_with_signing_key.post(
        "/v1/events/action", headers={"Authorization": f"Bearer {secret}"},
        json={"integration": "claude-code", "agent": "claude-code", "task": "t1", "tool": "Read",
              "arguments": {"file_path": "a.ts"}})
    assert raw_report.status_code == 200

    credential_report = client_with_signing_key.post(
        "/v1/events/action", headers={"Authorization": f"Bearer {token}"},
        json={"task": "t1", "tool": "Read", "arguments": {"file_path": "a.ts"}})
    assert credential_report.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
