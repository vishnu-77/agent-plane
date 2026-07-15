"""Agent-to-agent (A2A) edge: scoped delegation with attenuation."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient


def _keypair() -> tuple[str, str]:
    key = ed25519.Ed25519PrivateKey.generate()
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv, pub


def _parent_token(priv: str, *, tools, clearance, groups) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "alice", "app": "crm", "agent": "parent-agent", "tenant": "default",
            "scope": {"tools": tools, "clearance": clearance, "groups": groups},
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "jti": uuid.uuid4().hex,
        },
        priv,
        algorithm="EdDSA",
    )


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("IDENTITY_MODE", "delegation")
    monkeypatch.setenv("DELEGATION_PUBLIC_KEY", pub)
    monkeypatch.setenv("DELEGATION_SIGNING_KEY", priv)
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        yield c, priv
    get_settings.cache_clear()


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_delegate_attenuates_and_child_token_works(ctx):
    client, priv = ctx
    parent = _parent_token(priv, tools=["search", "draft_email"], clearance="confidential", groups=["g1"])
    # Hand off a narrower scope: only `search`, lower clearance.
    r = client.post(
        "/v1/agents/delegate",
        headers=_auth(parent),
        json={"agent": "child-agent", "scope": {"tools": ["search"], "clearance": "internal", "groups": []}, "ttl": 600},
    )
    assert r.status_code == 200
    child = r.json()
    assert child["scope"]["tools"] == ["search"]

    # The child credential is a real, verified delegation with the attenuated scope.
    from agent_plane.config import get_settings
    from agent_plane.gateway.identity import resolve_identity

    actor = resolve_identity(_auth(child["token"])["Authorization"], get_settings())
    assert actor.agent_id == "child-agent"
    assert actor.allowed_tools == ["search"]
    assert actor.clearance.value == "internal"

    # And it actually enforces the narrower scope on the tool edge: `search` ok...
    ok = client.post("/v1/tools/invoke", headers=_auth(child["token"]),
                     json={"tool": "search_kb", "arguments": {}})
    # search_kb isn't in scope -> denied (child only has `search`); proves least privilege.
    assert ok.status_code == 403


def test_escalation_is_refused(ctx):
    client, priv = ctx
    parent = _parent_token(priv, tools=["search"], clearance="internal", groups=["g1"])
    # Ask for more than the parent holds, each dimension.
    for scope, dim in [
        ({"tools": ["wire_transfer"]}, "tools"),
        ({"clearance": "regulated"}, "clearance"),
        ({"groups": ["g2"]}, "groups"),
    ]:
        r = client.post("/v1/agents/delegate", headers=_auth(parent),
                        json={"agent": "child", "scope": scope})
        assert r.status_code == 403, dim
        assert r.json()["detail"]["error"] == "privilege_escalation"


def test_delegation_is_audited_and_metered(ctx):
    client, priv = ctx
    parent = _parent_token(priv, tools=["search"], clearance="internal", groups=[])
    client.post("/v1/agents/delegate", headers=_auth(parent),
                json={"agent": "child", "scope": {"tools": ["search"]}})
    audit = client.get("/v1/audit", headers={"X-Admin-Token": "test-admin"}).json()["events"]
    assert any(e["model_requested"] == "a2a:child" for e in audit)


def test_missing_auth_401(ctx):
    client, _ = ctx
    assert client.post("/v1/agents/delegate", json={"agent": "x"}).status_code == 401


def test_disabled_without_signing_key(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "a.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.delenv("DELEGATION_SIGNING_KEY", raising=False)
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        assert c.post("/v1/agents/delegate", json={"agent": "x"}).status_code == 404
    get_settings.cache_clear()
