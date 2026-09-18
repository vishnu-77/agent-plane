"""Adoption funnel (Phase 32): product/distribution evidence, not just
security benchmarks. Computed from state that's already authoritative
elsewhere (accounts' Integration status, the agent registry's first_seen,
ContractRegistry, and the registry's own enforcement mode) - no separate
event-tracking system, so it can never disagree with what those systems
already record.

    install -> connected -> first observed event -> first agent discovered
        -> contract generated -> contract accepted -> enforce enabled

The product goal this answers: a developer should see meaningful data
before needing to understand the theory behind it - so this reports
*when* each step happened, not just whether it did.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, Query, Request

from agent_plane.gateway.authz import require_admin

adoption_router = APIRouter()


def compute_adoption_funnel(request: Request, tenant: str) -> dict[str, Any]:
    accounts = getattr(request.app.state, "accounts", None)
    registry = getattr(request.app.state, "agent_registry", None)
    contracts = getattr(request.app.state, "contracts", None)
    settings = request.app.state.settings

    integrations = accounts.integrations(tenant) if accounts is not None else []
    connected = [i for i in integrations if i.status == "connected"]
    connected_at = min((i.created_at for i in connected), default=None)

    agents = registry.agents(tenant) if registry is not None else []
    first_agent_discovered_at = min((a.first_seen for a in agents), default=None)
    first_observed_event_at = min((a.first_seen for a in agents), default=None)  # observe() sets first_seen on first event

    tenant_contracts = contracts.all_for_tenant(tenant) if contracts is not None else []
    contract_generated_at = min((c.created_at for c in tenant_contracts), default=None)
    accepted = [c for c in tenant_contracts if c.approved_by]
    contract_accepted_at = min((c.created_at for c in accepted), default=None)

    # Same precedence as AuthorityService._mode(): a project's own mode
    # wins; registry.mode()/settings.enforcement_mode is only the fallback
    # for tenants with no project record.
    project = accounts.project(tenant) if accounts is not None else None
    if project is not None:
        effective_mode = project.mode
    elif registry is not None:
        effective_mode = registry.mode(tenant, settings.enforcement_mode)
    else:
        effective_mode = settings.enforcement_mode
    enforce_enabled = effective_mode == "enforce"

    def step(name: str, at: datetime | None) -> dict[str, Any]:
        return {"step": name, "reached": at is not None, "at": at.isoformat() if at else None}

    return {
        "tenant": tenant,
        "funnel": [
            step("connected", connected_at),
            step("first_observed_event", first_observed_event_at),
            step("first_agent_discovered", first_agent_discovered_at),
            step("contract_generated", contract_generated_at),
            step("contract_accepted", contract_accepted_at),
            # No timestamp source for when enforce mode was last flipped -
            # registry.set_mode() doesn't record one (see registry/store.py).
            {"step": "enforce_enabled", "reached": enforce_enabled, "at": None},
        ],
    }


@adoption_router.get("/v1/adoption-funnel")
async def adoption_funnel(request: Request, tenant: str = Query(default="default"),
                          x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(request, x_admin_token, tenant=tenant)
    return compute_adoption_funnel(request, tenant)
