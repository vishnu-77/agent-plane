"""Verifiable agent identity (identity_mode=delegation)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from agent_plane.config import Settings
from agent_plane.gateway.identity import IdentityError, resolve_identity


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


def _token(priv_pem: str, **override) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": "alice",
        "app": "crm",
        "agent": "sales-assistant",
        "tenant": "default",
        "scope": {"tools": ["search", "draft_email"], "clearance": "confidential"},
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=3600)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    payload.update(override)
    return jwt.encode(payload, priv_pem, algorithm="EdDSA")


def _settings(pub_pem: str, **over) -> Settings:
    return Settings(identity_mode="delegation", delegation_public_key=pub_pem, **over)


def _auth(token: str) -> str:
    return f"Bearer {token}"


def test_valid_delegation_grants_scoped_actor():
    priv, pub = _keypair()
    actor = resolve_identity(_auth(_token(priv)), _settings(pub))
    assert actor.user_id == "alice"
    assert actor.app_id == "crm"
    assert actor.agent_id == "sales-assistant"
    # Tools + clearance come from the verified scope, not a self-asserted claim.
    assert actor.allowed_tools == ["search", "draft_email"]
    assert actor.clearance.value == "confidential"


def test_tampered_token_rejected():
    priv, pub = _keypair()
    token = _token(priv)
    tampered = token[:-4] + ("AAAA" if token[-4:] != "AAAA" else "BBBB")
    with pytest.raises(IdentityError):
        resolve_identity(_auth(tampered), _settings(pub))


def test_wrong_issuer_key_rejected():
    priv, _ = _keypair()
    _, other_pub = _keypair()
    with pytest.raises(IdentityError):
        resolve_identity(_auth(_token(priv)), _settings(other_pub))


def test_expired_delegation_rejected():
    priv, pub = _keypair()
    past = datetime.now(UTC) - timedelta(hours=2)
    token = _token(
        priv,
        iat=int(past.timestamp()),
        exp=int((past + timedelta(seconds=60)).timestamp()),
    )
    with pytest.raises(IdentityError):
        resolve_identity(_auth(token), _settings(pub))


def test_revoked_jti_rejected():
    priv, pub = _keypair()
    jid = uuid.uuid4().hex
    token = _token(priv, jti=jid)
    with pytest.raises(IdentityError):
        resolve_identity(_auth(token), _settings(pub, revoked_jtis=jid))


def test_agent_cannot_self_assert_scope():
    # A token that *claims* top-level allowed_tools is ignored; only the signed
    # scope counts. Here scope grants nothing, so the actor gets no tools.
    priv, pub = _keypair()
    token = _token(priv, scope={"tools": [], "clearance": "internal"}, allowed_tools=["wire_transfer"])
    actor = resolve_identity(_auth(token), _settings(pub))
    assert actor.allowed_tools == []


def test_runtime_revocation_blocks_token():
    # Not configured-revoked, but revoked live via the admin API's set.
    priv, pub = _keypair()
    jid = uuid.uuid4().hex
    token = _token(priv, jti=jid)
    with pytest.raises(IdentityError):
        resolve_identity(_auth(token), _settings(pub), revoked={jid})


def test_claims_mode_unchanged():
    settings = Settings(identity_mode="jwt_claims", jwt_secret="x")
    token = jwt.encode(
        {"sub": "bob", "agent_id": "a1", "allowed_tools": ["t"]}, "x", algorithm="HS256"
    )
    actor = resolve_identity(_auth(token), settings)
    assert actor.agent_id == "a1"
    assert actor.allowed_tools == ["t"]
