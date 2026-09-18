"""Derive a PrincipalIdentity from an already-resolved Actor.

Pure function, called by nothing in the request path yet (PR-3). Reflects
what's true today without changing it: an actor tied to a resolvable
Project API Key is api_key_bound; otherwise self_asserted.
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
) -> PrincipalIdentity:
    assurance = IdentityAssurance.API_KEY_BOUND if api_key_id else IdentityAssurance.SELF_ASSERTED
    return PrincipalIdentity(
        principal_id=api_key_id or actor.agent_id or actor.user_id,
        issuer=api_key_id,
        subject=actor.agent_id or actor.user_id,
        trust_domain=trust_domain or default_trust_domain_id(actor.tenant),
        assurance=assurance,
    )
