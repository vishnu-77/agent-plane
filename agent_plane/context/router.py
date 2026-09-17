"""Context-plane APIs.

The registry is explanatory: it records what can influence an agent and links
those assets to decisions. It never grants authority. Memory read/write checks
reuse AuthorityService so persistent context crosses the same boundary as any
other action.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request

from agent_plane.gateway.authz import resolve_operator
from agent_plane.gateway.context import resolve_request

context_router = APIRouter(tags=["context"])


def _scope(request: Request, admin: str | None, demo: str | None, project: str | None):
    return resolve_operator(request, admin, demo, project=project)


@context_router.get("/v1/context/assets")
async def list_context_assets(
    request: Request,
    project: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=1000),
    x_admin_token: str | None = Header(default=None),
    x_demo_token: str | None = Header(default=None),
) -> dict[str, Any]:
    scope = _scope(request, x_admin_token, x_demo_token, project)
    tenant = scope.restrict(project)
    items = request.app.state.context_store.assets(tenant, kind=kind, limit=limit)
    return {"assets": [a.public() for a in items], "count": len(items)}


@context_router.get("/v1/context/assets/{asset_id}")
async def get_context_asset(
    request: Request,
    asset_id: str,
    project: str | None = Query(default=None),
    x_admin_token: str | None = Header(default=None),
    x_demo_token: str | None = Header(default=None),
) -> dict[str, Any]:
    scope = _scope(request, x_admin_token, x_demo_token, project)
    tenant = scope.restrict(project)
    if tenant is None:
        raise HTTPException(status_code=400, detail="project is required for context asset lookup")
    rec = request.app.state.context_store.asset(tenant, asset_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="context asset not found")
    snapshots = request.app.state.context_store.snapshots(tenant, asset_id, limit=25)
    return {"asset": rec.public(), "snapshots": [s.model_dump(mode="json") for s in snapshots]}


@context_router.get("/v1/context/changes")
async def context_changes(
    request: Request,
    project: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    x_admin_token: str | None = Header(default=None),
    x_demo_token: str | None = Header(default=None),
) -> dict[str, Any]:
    scope = _scope(request, x_admin_token, x_demo_token, project)
    tenant = scope.restrict(project)
    items = request.app.state.context_store.changes(tenant, limit=limit)
    return {"changes": [a.public() for a in items], "count": len(items)}


@context_router.get("/v1/context/lineage/{decision_id}")
async def context_lineage(
    request: Request,
    decision_id: str,
    x_admin_token: str | None = Header(default=None),
    x_demo_token: str | None = Header(default=None),
) -> dict[str, Any]:
    scope = _scope(request, x_admin_token, x_demo_token, None)
    event = request.app.state.audit.get(decision_id)
    if event is None or not scope.allows(event.get("tenant")):
        raise HTTPException(status_code=404, detail="decision not found")
    tenant = str(event["tenant"])
    lineage = request.app.state.context_store.lineage(tenant, decision_id)
    if lineage is None:
        return {"decision_id": decision_id, "lineage": None, "assets": []}
    assets = [request.app.state.context_store.asset(tenant, aid) for aid in lineage.asset_ids]
    return {
        "decision_id": decision_id,
        "lineage": lineage.model_dump(mode="json"),
        "assets": [a.public() for a in assets if a is not None],
    }


@context_router.post("/v1/context/register")
async def register_context(
    request: Request,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    ctx = resolve_request(request, authorization=authorization, x_api_key=x_api_key, body=body)
    if ctx.project is None:
        raise HTTPException(status_code=401, detail="A Project API Key is required")
    ctx.requires("ingest")
    raw = body.get("assets")
    if raw is None and isinstance(body.get("asset"), dict):
        raw = [body["asset"]]
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="'assets' must be a list")
    if len(raw) > 100:
        raise HTTPException(status_code=400, detail="at most 100 context assets may be registered at once")
    task = str(body.get("task") or "")[:200] or None
    registered = request.app.state.context_store.register_many(
        ctx.project.id, [a for a in raw if isinstance(a, dict)], agent=ctx.agent, task=task,
    )
    return {"registered": [a.public() for a in registered], "count": len(registered)}


@context_router.post("/v1/context/memory/authorize")
async def authorize_memory(
    request: Request,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Authorize a memory read/write without storing memory content itself."""
    ctx = resolve_request(request, authorization=authorization, x_api_key=x_api_key, body=body)
    if ctx.project is None:
        raise HTTPException(status_code=401, detail="A Project API Key is required")
    ctx.requires("ingest")
    operation = str(body.get("operation") or "").lower()
    if operation not in {"read", "write"}:
        raise HTTPException(status_code=400, detail="'operation' must be read or write")
    provider = str(body.get("provider") or "memory")[:120]
    scope_name = str(body.get("scope") or "default")[:200]
    record_ref = str(body.get("record_ref") or "")[:200]
    task = str(body.get("task") or ctx.session or "memory-task")[:200]
    resource = f"memory://{provider}/{scope_name}" + (f"/{record_ref}" if record_ref else "")

    assets = request.app.state.context_store.register_many(ctx.project.id, [{
        "kind": "memory",
        "name": provider,
        "source": f"memory://{provider}",
        "trust": str(body.get("trust") or "project-bound"),
        "influence": "high",
        "metadata": {
            "scope": scope_name,
            "persistent": bool(body.get("persistent", True)),
            "cross_session": bool(body.get("cross_session", True)),
            "classification": str(body.get("classification") or "internal"),
        },
    }], agent=ctx.agent, task=task)

    actor = ctx.actor.model_copy(update={"agent_id": str(body.get("agent") or ctx.agent)[:200]})
    decision = request.app.state.authority.decide(
        actor,
        task=task,
        action=f"memory.{operation}",
        resource=resource,
        impact=str(body.get("impact") or "reversible"),
        context={"memory_provider": provider, "memory_scope": scope_name},
        edge="custom",
        integration="custom",
    )
    request.app.state.context_store.link_decision(
        ctx.project.id, decision.decision_id, [a.id for a in assets], task=task, agent=actor.agent_id,
    )
    payload = dict(decision.payload)
    payload.update({
        "action": f"memory.{operation}", "resource": resource,
        "context_assets": [a.id for a in assets],
        "binding": bool(decision.enforced),
    })
    return payload
