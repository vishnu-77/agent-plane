"""Trust domains. See spec/trust-domains.md.

Project (tenant) stays the sole billing/isolation boundary - untouched by
this module. A TrustDomain is an orthogonal statement about how an identity
was issued/verified. default_trust_domain_id() gives every existing actor a
synthetic 1:1 domain matching its tenant, so this module changes no
observable behavior until an operator registers a real domain and something
later starts consuming it instead of the default.
"""
from __future__ import annotations

from pydantic import BaseModel


def default_trust_domain_id(tenant: str) -> str:
    return f"tenant:{tenant}"


class TrustDomain(BaseModel):
    id: str
    name: str
    issuer: str | None = None
    environment: str = "production"   # production | staging | development


class TrustDomainRegistry:
    """In-memory only.

    ponytail: no persistence yet - add a TrustDomainRow (mirrors AgentRow
    in agent_plane/registry/store.py) when an operator needs a registered
    trust domain to survive a restart, not before.
    """

    def __init__(self) -> None:
        self._domains: dict[str, TrustDomain] = {}

    def get(self, domain_id: str) -> TrustDomain | None:
        return self._domains.get(domain_id)

    def upsert(self, domain: TrustDomain) -> None:
        self._domains[domain.id] = domain
