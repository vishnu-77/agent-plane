"""Console bootstrap endpoint.

The console previously opened four concurrent HTTP requests for its first
screen (system, decisions, agents, approvals). On serverless hosts that can
fan one cold page view into several cold function invocations. This endpoint
keeps the existing APIs intact while allowing the UI to fetch the same data in
one request.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Query, Request

from agent_plane.gateway.authz import resolve_operator
from agent_plane.registry.router import list_agents, list_decisions, system_state

console_api_router = APIRouter(tags=["console"])


@console_api_router.get("/v1/console/bootstrap")
async def console_bootstrap(
    request: Request,
    project: str = Query(..., min_length=1),
    decision_limit: int = Query(default=80, ge=1, le=200),
    x_admin_token: str | None = Header(default=None),
    x_demo_token: str | None = Header(default=None),
) -> dict[str, Any]:
    # Reuse the exact public read paths so bootstrap cannot drift into a
    # different interpretation of agents/decisions/system state.
    system = await system_state(
        request, tenant=project, x_admin_token=x_admin_token, x_demo_token=x_demo_token,
    )
    decisions = await list_decisions(
        request, tenant=project, limit=decision_limit,
        x_admin_token=x_admin_token, x_demo_token=x_demo_token,
    )
    agents = await list_agents(
        request, tenant=project, x_admin_token=x_admin_token, x_demo_token=x_demo_token,
    )
    scope = resolve_operator(request, x_admin_token, x_demo_token, project=project)
    tenant = scope.restrict(project)
    approvals = request.app.state.approvals.list(status="pending", tenant=tenant, limit=100)
    return {
        "system": system,
        "decisions": decisions["decisions"],
        "agents": agents["agents"],
        "approvals": [a.model_dump(mode="json") for a in approvals],
    }
