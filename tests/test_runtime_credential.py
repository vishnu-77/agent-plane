"""PR-5: /v1/auth/exchange runtime credential binding.

Reuses the A2A EdDSA keypair (see tests/test_delegation_identity.py for the
same _keypair() pattern). Every existing bearer-token shape must keep
working unchanged; a runtime credential must never be mistaken for, or
mistakable as, an A2A child / delegation token, and vice versa.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from agent_plane.config import Settings
from agent_plane.gateway.identity import IdentityError, resolve_identity
from agent_plane.gateway.runtime_credential import (
    ISS,
    TYP,
    mint_runtime_credential,
    verify_runtime_credential,
)


def _keypair() -> tuple[str, str]:
    key = ed25519.Ed25519PrivateKey.generate()
    priv = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv, pub


def _settings(priv: str | None = None, pub: str | None = None, **over) -> Settings:
    return Settings(delegation_signing_key=priv, delegation_public_key=pub, **over)


def test_mint_returns_none_without_signing_key():
    settings = _settings(priv=None, pub=None)
    assert mint_runtime_credential(settings, api_key_id="key_1", tenant="acme", agent="claude-code",
                                   session="ses_1", integration="claude-code",
                                   capabilities=["repo.read"], scopes=("ingest",)) is None


def test_mint_verify_round_trip():
    priv, pub = _keypair()
    settings = _settings(priv=priv, pub=pub)
    credential = mint_runtime_credential(settings, api_key_id="key_1", tenant="acme", agent="claude-code",
                                         session="ses_1", integration="claude-code",
                                         capabilities=["repo.read"], scopes=("ingest",))
    assert credential is not None and "token" in credential and "expires_at" in credential

    claims = verify_runtime_credential(credential["token"], settings)
    assert claims is not None
    assert claims["typ"] == TYP and claims["iss"] == ISS
    assert claims["sub"] == "key_1" and claims["tenant"] == "acme" and claims["agent"] == "claude-code"
    assert claims["scope"] == {"capabilities": ["repo.read"], "scopes": ["ingest"]}


def test_verify_returns_none_without_public_key():
    priv, pub = _keypair()
    mint_settings = _settings(priv=priv, pub=pub)
    credential = mint_runtime_credential(mint_settings, api_key_id="key_1", tenant="acme", agent="a",
                                         session=None, integration=None, capabilities=[], scopes=())
    verify_settings = _settings(priv=None, pub=None)
    assert verify_runtime_credential(credential["token"], verify_settings) is None


def test_verify_rejects_expired_token():
    priv, pub = _keypair()
    settings = _settings(priv=priv, pub=pub)
    now = datetime.now(UTC)
    token = jwt.encode({
        "typ": TYP, "iss": ISS, "sub": "key_1", "tenant": "acme", "agent": "a",
        "scope": {}, "iat": int((now - timedelta(hours=2)).timestamp()),
        "exp": int((now - timedelta(hours=1)).timestamp()), "jti": uuid.uuid4().hex,
    }, priv, algorithm="EdDSA")
    assert verify_runtime_credential(token, settings) is None


def test_verify_rejects_revoked_jti():
    priv, pub = _keypair()
    settings = _settings(priv=priv, pub=pub)
    credential = mint_runtime_credential(settings, api_key_id="key_1", tenant="acme", agent="a",
                                         session=None, integration=None, capabilities=[], scopes=())
    jti = jwt.decode(credential["token"], pub, algorithms=["EdDSA"])["jti"]
    assert verify_runtime_credential(credential["token"], settings, revoked={jti}) is None


def test_verify_rejects_wrong_typ_or_iss():
    """An A2A-minted-shape token (no typ/iss claims at all) must never verify
    as a runtime credential."""
    priv, pub = _keypair()
    settings = _settings(priv=priv, pub=pub)
    now = datetime.now(UTC)
    a2a_shaped = jwt.encode({
        "sub": "alice", "app": "crm", "agent": "child-agent", "tenant": "acme",
        "scope": {"tools": ["search"], "clearance": "public", "groups": []},
        "iat": int(now.timestamp()), "exp": int((now + timedelta(hours=1)).timestamp()),
        "jti": uuid.uuid4().hex,
    }, priv, algorithm="EdDSA")
    assert verify_runtime_credential(a2a_shaped, settings) is None


def test_resolve_delegation_rejects_a_runtime_credential():
    """The inverse: a runtime credential must never be usable as a
    delegation identity by an endpoint that calls resolve_identity()
    directly (e.g. POST /v1/agents/delegate) - same keypair, different
    credential type."""
    priv, pub = _keypair()
    mint_settings = _settings(priv=priv, pub=pub)
    credential = mint_runtime_credential(mint_settings, api_key_id="key_1", tenant="acme", agent="claude-code",
                                         session=None, integration=None, capabilities=[], scopes=())
    delegation_settings = _settings(priv=None, pub=pub, identity_mode="delegation")
    with pytest.raises(IdentityError):
        resolve_identity(f"Bearer {credential['token']}", delegation_settings)


def test_a2a_child_token_is_not_accepted_as_a_runtime_credential():
    """And the forward direction again, exercised through the real A2A
    minting shape used by gateway/a2a.py (kept in sync manually - see that
    module for the authoritative claim set)."""
    priv, pub = _keypair()
    settings = _settings(priv=priv, pub=pub)
    now = datetime.now(UTC)
    child_token = jwt.encode({
        "sub": "alice", "app": "crm", "agent": "child-agent", "tenant": "acme",
        "parent_agent": "parent-agent",
        "scope": {"tools": ["search"], "clearance": "public", "groups": []},
        "iat": int(now.timestamp()), "exp": int((now + timedelta(hours=1)).timestamp()),
        "jti": uuid.uuid4().hex,
    }, priv, algorithm="EdDSA")
    assert verify_runtime_credential(child_token, settings) is None


if __name__ == "__main__":
    test_mint_returns_none_without_signing_key()
    test_mint_verify_round_trip()
    test_verify_returns_none_without_public_key()
    test_verify_rejects_expired_token()
    test_verify_rejects_revoked_jti()
    test_verify_rejects_wrong_typ_or_iss()
    test_resolve_delegation_rejects_a_runtime_credential()
    test_a2a_child_token_is_not_accepted_as_a_runtime_credential()
    print("ok")
