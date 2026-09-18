"""Domain packs (Phase 26/27). Core (agent_plane.authority.evaluator,
agent_plane.consequence) never imports a concrete adapter - it's config-
driven (ConsequenceCatalog, AuthorityLease/AuthorityContract) and
domain-agnostic by construction already, matching the roadmap's "core does
not know what payment.transfer means" principle. A DomainAdapter packages a
coherent starter contract and resource-parsing convention for one domain;
it doesn't teach core new logic.
"""
from __future__ import annotations
