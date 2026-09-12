"""System and evidence reads: agents, tasks, resources, lineage, decisions.

Operator (admin token) sees every tenant; the hosted demo viewer
(X-Demo-Token) sees only the isolated demo tenant. Nothing here is a
decision path - it is the explanation of decisions already made.

    GET  /v1/agents?tenant=                  the authority registry
    GET  /v1/agents/{id}                     one agent, with drift and lineage
    POST /v1/agents/{id}/quarantine          hold every action (operator)
    DELETE /v1/agents/{id}/quarantine
    GET  /v1/agents/{id}/drift               declared vs granted vs observed
    GET  /v1/agents/{id}/suggested-lease     Observe -> Enforce
    GET  /v1/tasks, /v1/tasks/{id}
    POST /v1/tasks                           register an intent (origin/prompt)
    GET  /v1/resources
    GET  /v1/lineage/{lease_id}
    GET  /v1/decisions?tenant=&limit=
    GET  /v1/decisions/{id}                  the full trace for one decision
    GET  /v1/system                          LIVE telemetry
    GET/PUT /admin/mode                      observe | enforce
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request

from agent_plane.authority.service import TRACE_SCHEMA, lineage
from agent_plane.gateway.authz import OperatorScope, require_admin, resolve_operator
from agent_plane.gateway.identity import IdentityError, resolve_identity
from agent_plane.registry.store import Origin

registry_router = APIRouter(tags=["registry"])


def _scope(request: Request, admin: str | None, demo: str | None) -> OperatorScope:
    return resolve_operator(request, admin, demo)


def _tenant_of(scope: OperatorScope, tenant: str | None) -> str | None:
    return scope.restrict(tenant)


def _agent_or_404(request: Request, scope: OperatorScope, tenant: str | None, agent_id: str):
    registry = request.app.state.agent_registry
    candidates = [tenant] if tenant else sorted({a.tenant for a in registry.agents(scope.tenant)})
    for t in candidates:
        if not scope.allows(t):
            continue
        rec = registry.agent(t, agent_id)
        if rec is not None:
            return rec
    raise HTTPException(status_code=404, detail="agent not found")


def _granted_actions(request: Request, tenant: str, agent: str) -> tuple[list[str], list[dict[str, Any]]]:
    store = request.app.state.leases
    leases = [ls for ls in store.list() if ls.subject == agent and ls.tenant == tenant]
    actions = sorted({a for ls in leases if not ls.revoked for a in ls.actions})
    return actions, [ls.model_dump(mode="json") for ls in leases]


# --------------------------------------------------------------------------- #
# Agents
# --------------------------------------------------------------------------- #
@registry_router.get("/v1/agents")
async def list_agents(request: Request, tenant: str | None = Query(default=None),
                      x_admin_token: str | None = Header(default=None),
                      x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    scope = _scope(request, x_admin_token, x_demo_token)
    registry = request.app.state.agent_registry
    items = []
    for rec in registry.agents(_tenant_of(scope, tenant)):
        granted, leases = _granted_actions(request, rec.tenant, rec.id)
        active = [ls for ls in leases if not ls["revoked"]]
        items.append({**rec.model_dump(mode="json"), "granted_authority": granted,
                      "active_lease": active[-1]["id"] if active else None, "leases": [ls["id"] for ls in leases],
                      "delegated_authority": [ls["id"] for ls in leases if ls.get("parent_lease")]})
    return {"agents": items, "count": len(items)}


@registry_router.get("/v1/agents/{agent_id}")
async def get_agent(request: Request, agent_id: str, tenant: str | None = Query(default=None),
                    x_admin_token: str | None = Header(default=None),
                    x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    scope = _scope(request, x_admin_token, x_demo_token)
    rec = _agent_or_404(request, scope, _tenant_of(scope, tenant), agent_id)
    granted, leases = _granted_actions(request, rec.tenant, rec.id)
    registry = request.app.state.agent_registry
    audit = request.app.state.audit
    decisions = [e for e in audit.query(tenant=rec.tenant, limit=500) if e.get("agent_id") == rec.id
                 and str(e.get("model_requested", "")).startswith("authorize:")][:50]
    return {
        **rec.model_dump(mode="json"),
        "granted_authority": granted,
        "leases": leases,
        "lineage": {ls["id"]: lineage(request.app.state.leases, ls["id"]) for ls in leases},
        "drift": registry.drift(rec.tenant, rec.id, granted),
        "tasks_detail": [t.model_dump(mode="json") for t in registry.tasks(rec.tenant) if rec.id in t.agents],
        "recent_decisions": decisions,
        "children": [a.id for a in registry.agents(rec.tenant) if a.parent_agent == rec.id],
    }


@registry_router.post("/v1/agents/{agent_id}/quarantine")
async def quarantine_agent(request: Request, agent_id: str, body: dict[str, Any] | None = None,
                           tenant: str | None = Query(default=None),
                           x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(request, x_admin_token)
    body = body or {}
    tenant = tenant or body.get("tenant") or "default"
    rec = request.app.state.agent_registry.set_quarantine(tenant, agent_id, on=True, by=body.get("by") or "admin",
                                                    note=body.get("note"))
    request.app.state.audit.record({
        "decision_id": f"az_{agent_id[:8]}_q", "user_id": "admin", "tenant": tenant, "agent_id": agent_id,
        "model_requested": f"agent-admin:{agent_id}", "model_used": agent_id, "data_classification": "",
        "decision": "quarantine", "reason": "AGENT_QUARANTINED", "rules_matched": [],
    })
    return {"agent": rec.model_dump(mode="json")}


@registry_router.delete("/v1/agents/{agent_id}/quarantine")
async def release_agent(request: Request, agent_id: str, tenant: str | None = Query(default=None),
                        x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(request, x_admin_token)
    rec = request.app.state.agent_registry.set_quarantine(tenant or "default", agent_id, on=False)
    if rec is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return {"agent": rec.model_dump(mode="json")}


@registry_router.get("/v1/agents/{agent_id}/drift")
async def agent_drift(request: Request, agent_id: str, tenant: str | None = Query(default=None),
                      x_admin_token: str | None = Header(default=None),
                      x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    scope = _scope(request, x_admin_token, x_demo_token)
    rec = _agent_or_404(request, scope, _tenant_of(scope, tenant), agent_id)
    granted, _ = _granted_actions(request, rec.tenant, rec.id)
    return request.app.state.agent_registry.drift(rec.tenant, rec.id, granted)


@registry_router.get("/v1/agents/{agent_id}/suggested-lease")
async def agent_suggested_lease(request: Request, agent_id: str, tenant: str | None = Query(default=None),
                                task: str | None = Query(default=None),
                                x_admin_token: str | None = Header(default=None),
                                x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    scope = _scope(request, x_admin_token, x_demo_token)
    rec = _agent_or_404(request, scope, _tenant_of(scope, tenant), agent_id)
    return {"lease": request.app.state.agent_registry.suggested_lease(rec.tenant, rec.id, task)}


# --------------------------------------------------------------------------- #
# Tasks and resources
# --------------------------------------------------------------------------- #
@registry_router.get("/v1/tasks")
async def list_tasks(request: Request, tenant: str | None = Query(default=None),
                     x_admin_token: str | None = Header(default=None),
                     x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    scope = _scope(request, x_admin_token, x_demo_token)
    items = [t.model_dump(mode="json") for t in request.app.state.agent_registry.tasks(_tenant_of(scope, tenant))]
    return {"tasks": items, "count": len(items)}


@registry_router.get("/v1/tasks/{task_id}")
async def get_task(request: Request, task_id: str, tenant: str | None = Query(default=None),
                   x_admin_token: str | None = Header(default=None),
                   x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    scope = _scope(request, x_admin_token, x_demo_token)
    registry = request.app.state.agent_registry
    candidates = [tenant] if tenant else sorted({t.tenant for t in registry.tasks(scope.tenant)})
    for t in candidates:
        if not scope.allows(t):
            continue
        rec = registry.task(t, task_id)
        if rec is not None:
            leases = [request.app.state.leases.get(lid) for lid in rec.leases]
            return {**rec.model_dump(mode="json"),
                    "leases_detail": [ls.model_dump(mode="json") for ls in leases if ls is not None],
                    "lineage": {lid: lineage(request.app.state.leases, lid) for lid in rec.leases}}
    raise HTTPException(status_code=404, detail="task not found")


@registry_router.post("/v1/tasks")
async def register_task(request: Request, body: dict[str, Any],
                        authorization: str | None = Header(default=None),
                        x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    """Register an intent: who asked for what, and from which prompt or event.
    Admin callers may name the tenant; agent callers register for their own."""
    body = body or {}
    task = body.get("task")
    if not task or not isinstance(task, str):
        raise HTTPException(status_code=400, detail="'task' is required")
    settings = request.app.state.settings
    tenant: str
    agent: str | None = body.get("agent")
    if settings.admin_token and x_admin_token == settings.admin_token:
        tenant = body.get("tenant") or "default"
    else:
        try:
            actor = resolve_identity(authorization, settings, request.app.state.revocations)
        except IdentityError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        tenant = actor.tenant
        agent = agent or actor.agent_id or actor.user_id
    origin_raw = body.get("origin") or {}
    if not isinstance(origin_raw, dict):
        raise HTTPException(status_code=400, detail="'origin' must be an object")
    text = origin_raw.get("text")
    if text is not None and (not isinstance(text, str) or len(text) > 4000):
        raise HTTPException(status_code=400, detail="'origin.text' must be a string of at most 4000 chars")
    origin = Origin(kind=str(origin_raw.get("kind") or "api"), ref=origin_raw.get("ref"), text=text,
                    created_by=origin_raw.get("created_by"), parent_task=origin_raw.get("parent_task"),
                    parent_agent=origin_raw.get("parent_agent"))
    rec = request.app.state.agent_registry.register_task(tenant=tenant, task=task, origin=origin, agent=agent)
    return {"registered": True, "task": rec.model_dump(mode="json")}


@registry_router.get("/v1/resources")
async def list_resources(request: Request, tenant: str | None = Query(default=None),
                         x_admin_token: str | None = Header(default=None),
                         x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    scope = _scope(request, x_admin_token, x_demo_token)
    catalog = request.app.state.catalog
    touched = request.app.state.agent_registry.resources(_tenant_of(scope, tenant))
    for entry in touched:
        profile = catalog.resource_profile(entry["resource"])
        entry["profile"] = profile.model_dump(mode="json") if profile else None
        entry["downstream"] = catalog.dependents_of(entry["resource"])
    return {"resources": touched, "catalog": [r.model_dump(mode="json") for r in catalog.resources],
            "actions": [a.model_dump(mode="json") for a in catalog.actions]}


# --------------------------------------------------------------------------- #
# Lineage, decisions, system
# --------------------------------------------------------------------------- #
@registry_router.get("/v1/lineage/{lease_id}")
async def get_lineage(request: Request, lease_id: str,
                      x_admin_token: str | None = Header(default=None),
                      x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    scope = _scope(request, x_admin_token, x_demo_token)
    lease = request.app.state.leases.get(lease_id)
    if lease is None or not scope.allows(lease.tenant):
        raise HTTPException(status_code=404, detail="lease not found")
    chain = lineage(request.app.state.leases, lease_id)
    children = [ls.model_dump(mode="json") for ls in request.app.state.leases.list() if ls.parent_lease == lease_id]
    return {"lease": lease_id, "lineage": chain, "children": children}


def _trace_of(event: dict[str, Any]) -> dict[str, Any] | None:
    for item in event.get("obligations_applied") or []:
        if isinstance(item, dict) and item.get("schema") == TRACE_SCHEMA:
            return item
    return None


@registry_router.get("/v1/decisions")
async def list_decisions(request: Request, tenant: str | None = Query(default=None),
                         limit: int = Query(default=50, ge=1, le=500),
                         x_admin_token: str | None = Header(default=None),
                         x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    scope = _scope(request, x_admin_token, x_demo_token)
    events = request.app.state.audit.query(tenant=_tenant_of(scope, tenant), limit=limit * 3)
    items = []
    for e in events:
        trace = _trace_of(e)
        if trace is None:
            continue
        items.append({"decision_id": e["decision_id"], "created_at": e["created_at"], "tenant": e["tenant"],
                      "agent": e.get("agent_id") or e.get("user_id"), "task": trace["task"]["id"],
                      "action": trace["action"]["name"], "resource": trace["resource"]["name"],
                      "outcome": trace["decision"]["outcome"], "reason": trace["decision"]["reason"],
                      "would_be": trace["decision"].get("would_be"), "enforced": trace["decision"].get("enforced", True),
                      "lease": trace["authority"].get("lease"), "edge": trace.get("edge"),
                      "impact": (trace.get("consequence") or {}).get("impact"),
                      "environment": (trace.get("consequence") or {}).get("environment"),
                      "approval_id": trace["decision"].get("approval_id")})
        if len(items) >= limit:
            break
    return {"decisions": items, "count": len(items)}


@registry_router.get("/v1/decisions/{decision_id}")
async def get_decision(request: Request, decision_id: str,
                       x_admin_token: str | None = Header(default=None),
                       x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    scope = _scope(request, x_admin_token, x_demo_token)
    event = request.app.state.audit.get(decision_id)
    if event is None or not scope.allows(event["tenant"]):
        raise HTTPException(status_code=404, detail="decision not found")
    trace = _trace_of(event)
    related = []
    if trace and trace["decision"].get("approval_id"):
        req = request.app.state.approvals.get(trace["decision"]["approval_id"])
        related.append({"kind": "approval", "approval": req.model_dump(mode="json") if req else None})
    receipts = [e for e in request.app.state.audit.query(tenant=event["tenant"], limit=300)
                if any(isinstance(o, dict) and o.get("schema") == "agent-plane.gateway.v1"
                       and o.get("admission_id") == decision_id and o.get("phase") == "execution"
                       for o in e.get("obligations_applied") or [])]
    return {"decision_id": decision_id, "event": event, "trace": trace, "related": related, "receipts": receipts}


@registry_router.get("/v1/system")
async def system_state(request: Request, tenant: str | None = Query(default=None),
                       x_admin_token: str | None = Header(default=None),
                       x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    """LIVE telemetry for the console's first screen."""
    scope = _scope(request, x_admin_token, x_demo_token)
    tenant = _tenant_of(scope, tenant)
    registry = request.app.state.agent_registry
    settings = request.app.state.settings
    agents = registry.agents(tenant)
    tasks = registry.tasks(tenant)
    recent_cut = datetime.now(UTC) - timedelta(minutes=30)
    active_agents = [a for a in agents if a.last_seen.replace(tzinfo=a.last_seen.tzinfo or UTC) >= recent_cut
                     or a.status == "active"]
    counts: dict[str, int] = {}
    for a in agents:
        for k, v in a.decisions.items():
            counts[k] = counts.get(k, 0) + v
    pending = request.app.state.approvals.list(status="pending", tenant=tenant, limit=500)
    mode_tenant = tenant or "default"
    return {
        "mode": registry.mode(mode_tenant, settings.enforcement_mode),
        "enforcement_default": settings.enforcement_mode,
        "identity_mode": settings.identity_mode,
        "environment": settings.environment,
        "authority_store": settings.authority_store,
        "policy_version": getattr(getattr(request.app.state, "engine", None), "bundle", None).version
        if getattr(request.app.state, "engine", None) else None,
        "tenant": tenant,
        "demo": scope.demo,
        "agents": len(agents), "active_agents": len(active_agents),
        "quarantined": len([a for a in agents if a.status == "quarantined"]),
        "tasks": len([t for t in tasks if t.status == "active"]),
        "decisions": counts,
        "pending_approvals": len(pending),
        "leases": len([ls for ls in request.app.state.leases.list() if (tenant is None or ls.tenant == tenant)]),
        "audit_head": (request.app.state.audit.recent(limit=1) or [{}])[0].get("event_hash"),
    }


@registry_router.get("/admin/mode")
async def get_mode(request: Request, tenant: str | None = Query(default=None),
                   x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(request, x_admin_token)
    settings = request.app.state.settings
    return {"tenant": tenant or "*", "mode": request.app.state.agent_registry.mode(tenant or "default", settings.enforcement_mode),
            "default": settings.enforcement_mode}


@registry_router.put("/admin/mode")
async def set_mode(request: Request, body: dict[str, Any],
                   x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(request, x_admin_token)
    mode = (body or {}).get("mode")
    if mode not in ("observe", "enforce"):
        raise HTTPException(status_code=400, detail="'mode' must be observe or enforce")
    tenant = (body or {}).get("tenant")
    request.app.state.agent_registry.set_mode(tenant, mode)
    request.app.state.audit.record({
        "decision_id": f"admin_mode_{mode}", "user_id": "admin", "tenant": tenant or "*",
        "model_requested": "admin:mode", "model_used": mode, "data_classification": "",
        "decision": "admin_action", "reason": f"enforcement mode -> {mode}", "rules_matched": [],
    })
    return {"tenant": tenant or "*", "mode": mode}
