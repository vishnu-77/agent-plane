"""Derive a PrincipalIdentity from an already-resolved Actor.

Pure function. Reflects what's true today without changing it: an actor
tied to a resolvable Project API Key (and, once wired, a registered
connector) is connector_authenticated; otherwise reported.
"""
from __future__ import annotations

from agent_plane.identity.assurance import IdentityAssurance
from agent_plane.identity.models import PrincipalIdentity
from agent_plane.identity.trust import default_trust_domain_id
from agent_plane.schemas.canonical import Actor


def resolve_principal(
    actor: Actor,
    *,
    api_key_id: str | None = None,
    trust_domain: str | None = None,
    assurance: IdentityAssurance | None = None,
    authentication_method: str | None = None,
) -> PrincipalIdentity:
    if assurance is None:
        assurance = actor.assurance or (
            IdentityAssurance.CONNECTOR_AUTHENTICATED if api_key_id else IdentityAssurance.REPORTED
        )
    return PrincipalIdentity(
        principal_id=api_key_id or actor.agent_id or actor.user_id,
        principal_type="agent",
        issuer=api_key_id,
        subject=actor.agent_id or actor.user_id,
        trust_domain=trust_domain or actor.trust_domain or default_trust_domain_id(actor.tenant),
        assurance=assurance,
        authentication_method=authentication_method,
    )
