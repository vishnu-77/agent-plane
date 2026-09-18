"""PrincipalIdentity. See spec/principals.md.

Deliberately thin: opaque id/string fields, no issuer/JWKS verification
machinery. Nothing consumes this yet (PR-3 ships it unwired) - build out
verification only once a real caller (PR-5's runtime credential) proves
what's actually needed.
"""
from __future__ import annotations

from pydantic import BaseModel

from agent_plane.identity.assurance import IdentityAssurance


class PrincipalIdentity(BaseModel):
    principal_id: str
    issuer: str | None = None
    subject: str | None = None
    trust_domain: str | None = None
    assurance: IdentityAssurance = IdentityAssurance.SELF_ASSERTED
