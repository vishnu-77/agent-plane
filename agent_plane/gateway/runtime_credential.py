"""Runtime credential: a short-lived, EdDSA-signed identity minted by
``POST /v1/auth/exchange`` (PR-5). Reuses the A2A key material
(``settings.delegation_signing_key_pem`` / ``delegation_public_key_pem`` -
see ``gateway/a2a.py``) rather than inventing a new token format.

Distinguished from every other bearer-token shape in use today by two
required, exact-match claims: ``typ == "agentplane.runtime"`` and
``iss == "agent-plane:exchange"``. An A2A child token carries no ``typ``
claim at all, and a dev/delegation identity token carries neither claim -
neither can ever be misread as a runtime credential, and this credential
can never be misread as either of them. See spec/identity-assurance.md.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from agent_plane.config import Settings

TYP = "agentplane.runtime"
ISS = "agent-plane:exchange"


def mint_runtime_credential(
    settings: Settings, *, api_key_id: str, tenant: str, agent: str,
    session: str | None, integration: str | None,
    capabilities: list[str], scopes: tuple[str, ...],
) -> dict[str, Any] | None:
    """Returns ``{"token": ..., "expires_at": ...}``, or None if runtime
    credentials aren't configured (no ``delegation_signing_key``) - callers
    must treat None as "omit the field", not an error."""
    signing_key = settings.delegation_signing_key_pem
    if not signing_key:
        return None
    now = datetime.now(UTC)
    ttl = min(900, settings.max_delegation_ttl_seconds)
    exp = now + timedelta(seconds=ttl)
    jti = uuid.uuid4().hex
    token = jwt.encode(
        {
            "typ": TYP,
            "iss": ISS,
            "sub": api_key_id,          # the underlying Project API Key id - preserved for
                                         # accountability and so ctx.requires() keeps working
            "tenant": tenant,
            "agent": agent,
            "session": session,
            "integration": integration,
            # Round-trips what the raw key would have asserted per request, so
            # scope enforcement behaves identically for either credential.
            "scope": {"capabilities": capabilities, "scopes": list(scopes)},
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
            "jti": jti,
        },
        signing_key,
        algorithm="EdDSA",
    )
    return {"token": token, "expires_at": exp.isoformat()}


def verify_runtime_credential(
    token: str, settings: Settings, revoked: set[str] | None = None,
) -> dict[str, Any] | None:
    """Verify a runtime credential. Returns its claims on success, or None on
    ANY failure (bad signature, wrong claims, expired, revoked, feature not
    configured). Callers must fall through to the existing identity
    resolution path on None rather than raising - this function never
    rejects a token shape it doesn't recognize, it just declines to handle it.

    ponytail: a runtime credential isn't re-checked against live Project API
    Key revocation until it expires (bounded by its own short TTL, <=15 min).
    Add jti revocation on key-revoke if an operator needs sub-TTL revocation.
    """
    public_key = settings.delegation_public_key_pem
    if not public_key:
        return None
    try:
        claims = jwt.decode(token, public_key, algorithms=["EdDSA"])
    except jwt.PyJWTError:
        return None
    if claims.get("typ") != TYP or claims.get("iss") != ISS:
        return None
    jti = claims.get("jti")
    revoked_ids = settings.revoked_jti_set | (revoked or set())
    if jti and jti in revoked_ids:
        return None
    if not claims.get("sub") or not claims.get("tenant") or not claims.get("agent"):
        return None
    return claims
