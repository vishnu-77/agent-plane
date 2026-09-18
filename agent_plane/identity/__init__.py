"""Principal identity: who/what is acting, and how strongly we know it.

See spec/principals.md and spec/identity-assurance.md. This package is
additive and, as of PR-3/PR-4, not yet called from the request path -
``Actor`` (agent_plane/schemas/canonical.py) remains what the evaluator,
enforcement service, and policy engine consume today.
"""
from __future__ import annotations

from agent_plane.identity.assurance import IdentityAssurance
from agent_plane.identity.models import PrincipalIdentity
from agent_plane.identity.resolver import resolve_principal
from agent_plane.identity.trust import TrustDomain, TrustDomainRegistry, default_trust_domain_id

__all__ = [
    "IdentityAssurance",
    "PrincipalIdentity",
    "resolve_principal",
    "TrustDomain",
    "TrustDomainRegistry",
    "default_trust_domain_id",
]
