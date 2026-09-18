"""Identity resolution: token -> Actor.

Two pluggable modes, selected by ``settings.identity_mode`` (no code change to
switch):

- ``jwt_claims`` - decode an HS256 token and trust its claims. Fine for dev and
  simple integrations.
- ``delegation`` - verify an **Ed25519-signed delegation** issued by a trusted
  authority. The agent can no longer assert its own ``agent_id`` or
  ``allowed_tools``: identity, scope, expiry, and revocation are *verified*, not
  declared. This is the production answer to the confused-deputy problem.

Either way the rest of the data plane consumes the same :class:`Actor`.
"""
from __future__ import annotations

import jwt

from agent_plane.config import Settings
from agent_plane.gateway.runtime_credential import TYP as RUNTIME_CREDENTIAL_TYP
from agent_plane.identity.assurance import IdentityAssurance
from agent_plane.identity.trust import default_trust_domain_id
from agent_plane.schemas.canonical import Actor, DataClassification


class IdentityError(Exception):
    """Raised when a request cannot be authenticated."""


def _clearance(value: str | None) -> DataClassification:
    try:
        return DataClassification(value) if value else DataClassification.INTERNAL
    except ValueError:
        return DataClassification.INTERNAL


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise IdentityError("Missing or malformed Authorization header")
    return authorization.split(" ", 1)[1].strip()


def resolve_identity(
    authorization: str | None,
    settings: Settings,
    revoked: set[str] | None = None,
) -> Actor:
    """Resolve a bearer token to an Actor.

    ``revoked`` is an optional runtime revocation set (e.g. populated live by the
    admin API); it is checked in addition to the configured revocation sources.
    """
    token = _bearer(authorization)
    if settings.identity_mode == "delegation":
        return _resolve_delegation(token, settings, revoked)
    return _resolve_claims(token, settings)


def _resolve_claims(token: str, settings: Settings) -> Actor:
    """Dev mode: trust the token's claims."""
    try:
        claims = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:  # noqa: BLE001 - normalize to 401
        raise IdentityError(f"Invalid token: {exc}") from exc

    user_id = claims.get("sub") or claims.get("user_id")
    if not user_id:
        raise IdentityError("Token missing subject (sub)")

    tenant = claims.get("tenant", "default")
    return Actor(
        user_id=str(user_id),
        tenant=tenant,
        department=claims.get("department"),
        app_id=claims.get("app_id"),
        agent_id=claims.get("agent_id"),
        clearance=_clearance(claims.get("clearance")),
        allowed_tools=claims.get("allowed_tools", []) or [],
        groups=claims.get("groups", []) or [],
        # A bare HS256 dev-mode token: the lowest assurance rung. See
        # spec/identity-assurance.md.
        assurance=IdentityAssurance.REPORTED,
        trust_domain=default_trust_domain_id(tenant),
    )


def _resolve_delegation(
    token: str, settings: Settings, revoked: set[str] | None = None
) -> Actor:
    """Production mode: verify a signed delegation; scope is authoritative."""
    public_key = settings.delegation_public_key_pem
    if not public_key:
        raise IdentityError(
            "identity_mode=delegation but no delegation_public_key is configured"
        )

    verify_kwargs: dict[str, str] = {}
    if settings.delegation_issuer:
        verify_kwargs["issuer"] = settings.delegation_issuer
    if settings.delegation_audience:
        verify_kwargs["audience"] = settings.delegation_audience

    try:
        # EdDSA + exp/iss/aud are verified here; a bad signature or an expired
        # token raises and becomes a 401.
        claims = jwt.decode(token, public_key, algorithms=["EdDSA"], **verify_kwargs)
    except jwt.PyJWTError as exc:  # noqa: BLE001 - normalize to 401
        raise IdentityError(f"Invalid delegation: {exc}") from exc

    # A runtime credential (PR-5, gateway/runtime_credential.py) is signed with
    # the same keypair A2A uses, but is a distinct credential type scoped to
    # /v1/auth/exchange's own claim shape - it must never be reinterpretable
    # as a delegation token by an endpoint that calls resolve_identity()
    # directly (e.g. POST /v1/agents/delegate).
    if claims.get("typ") == RUNTIME_CREDENTIAL_TYP:
        raise IdentityError("Runtime credentials cannot be used as a delegation identity")

    jti = claims.get("jti")
    revoked_ids = settings.revoked_jti_set | (revoked or set())
    if jti and jti in revoked_ids:
        raise IdentityError("Delegation has been revoked")

    user_id = claims.get("sub")
    if not user_id:
        raise IdentityError("Delegation missing subject (sub)")

    # The verified scope - not the agent's say-so - decides tools and clearance.
    scope = claims.get("scope") or {}
    tenant = claims.get("tenant", "default")
    return Actor(
        user_id=str(user_id),
        tenant=tenant,
        department=claims.get("department"),
        app_id=claims.get("app") or claims.get("app_id"),
        agent_id=claims.get("agent") or claims.get("agent_id"),
        clearance=_clearance(scope.get("clearance") or claims.get("clearance")),
        allowed_tools=scope.get("tools", []) or [],
        groups=scope.get("groups", claims.get("groups", [])) or [],
        # A cryptographically verified, signed delegation - the strongest
        # rung short of workload attestation. See spec/identity-assurance.md.
        assurance=IdentityAssurance.DELEGATED_VERIFIED,
        trust_domain=claims.get("trust_domain") or default_trust_domain_id(tenant),
    )
