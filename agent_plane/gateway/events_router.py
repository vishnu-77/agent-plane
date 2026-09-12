"""``POST /v1/events/action`` - the one endpoint every integration reports to.

A connector says what an agent is about to do (or just did). agent-plane
normalizes it, evaluates authority and consequence, records the evidence, and
answers. What the connector does with the answer depends on what it *can* do:
the MCP gateway refuses to dispatch, a coding-agent hook blocks the tool, an
SDK caller decides for itself. The response says which, so nothing implies
agent-plane blocked something it cannot block.

The project's data-collection policy is applied here, before anything is
stored: prompt text, tool arguments, and outputs are dropped unless a human
turned them on for that project.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from agent_plane.accounts.models import INTEGRATION_CATALOG
from agent_plane.authority.provenance import validate_context
from agent_plane.events.normalize import normalize_action
from agent_plane.gateway.context import RequestContext, resolve_request
from agent_plane.registry.store import Origin

events_router = APIRouter(tags=["events"])

MAX_BATCH = 50


def _origin_for(project, payload: dict[str, Any]) -> Origin:
    """Task provenance, filtered by the project's collection policy.

    The identifier and the shape of the origin are always kept - that is what
    makes a decision explainable. The *text* of a prompt is only kept when the
    project explicitly collects prompt content.
    """
    raw = payload if isinstance(payload, dict) else {}
    text = raw.get("text") if project.collects("prompt_content") else None
    return Origin(
        kind=str(raw.get("kind") or "prompt"),
        ref=str(raw.get("ref") or raw.get("id") or "") or None,
        text=str(text)[:4000] if text else None,
        created_by=str(raw.get("created_by") or "") or None,
        parent_task=str(raw.get("parent_task") or "") or None,
        parent_agent=str(raw.get("parent_agent") or "") or None,
    )


def _one(request: Request, ctx: RequestContext, event: dict[str, Any]) -> dict[str, Any]:
    project = ctx.project
    if project is None:
        raise HTTPException(status_code=401, detail="A Project API Key is required to report activity")

    action, resource = normalize_action(
        action=event.get("action"), tool=event.get("tool"), resource=event.get("resource"),
        arguments=event.get("arguments") if isinstance(event.get("arguments"), dict) else None,
        repository=event.get("repository"), branch=event.get("branch"),
    )
    task = str(event.get("task") or ctx.session or "untitled-task")[:200]
    agent = str(event.get("agent") or ctx.agent)[:200]
    integration = str(event.get("integration") or ctx.integration or "custom")
    if integration not in INTEGRATION_CATALOG:
        integration = "custom"

    # Provenance first: a task exists because something asked for it.
    registry = request.app.state.agent_registry
    if event.get("origin") or event.get("task"):
        registry.register_task(tenant=project.id, task=task, agent=agent,
                               origin=_origin_for(project, event.get("origin") or {}))

    try:
        context = validate_context(event.get("context"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    context.setdefault("framework", integration)
    if ctx.host and project.collects("session_metadata"):
        context.setdefault("host", ctx.host)
    if ctx.session and project.collects("session_metadata"):
        context.setdefault("session_id", ctx.session)
    if project.collects("tool_arguments") and isinstance(event.get("arguments"), dict):
        context.setdefault("arguments_recorded", "true")

    actor = ctx.actor.model_copy(update={"agent_id": agent})
    result = request.app.state.authority.decide(
        actor, task=task, action=action, resource=resource,
        impact=str(event.get("impact") or "reversible"),
        approval=event.get("approval"), context=context,
        edge=integration, integration=integration,
    )

    request.app.state.accounts.touch_integration(
        project_id=project.id, kind=integration, host=ctx.host, agent=agent)

    catalog = INTEGRATION_CATALOG[integration]
    enforcement = catalog["enforcement"]
    payload = dict(result.payload)
    payload.update({
        "action": action, "resource": resource, "task": task, "agent": agent,
        "enforcement": enforcement,
        # Be explicit: a decision only blocks if this connector can block.
        "binding": bool(result.enforced and enforcement in ("full", "partial")),
    })
    return payload


@events_router.post("/v1/events/action")
async def report_action(request: Request, body: dict[str, Any],
                        authorization: str | None = Header(default=None),
                        x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    """Report one action, or a batch of up to 50 under ``events``."""
    ctx = resolve_request(request, authorization=authorization, x_api_key=x_api_key, body=body)
    ctx.requires("ingest")
    batch = body.get("events")
    if batch is not None:
        if not isinstance(batch, list) or len(batch) > MAX_BATCH:
            raise HTTPException(status_code=400, detail=f"'events' must be a list of at most {MAX_BATCH}")
        return {"results": [_one(request, ctx, e if isinstance(e, dict) else {}) for e in batch]}
    return _one(request, ctx, body)


@events_router.post("/v1/sessions")
async def start_session(request: Request, body: dict[str, Any] | None = None,
                        authorization: str | None = Header(default=None),
                        x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    """Announce an agent session. Optional: reporting actions creates one anyway."""
    body = body or {}
    ctx = resolve_request(request, authorization=authorization, x_api_key=x_api_key, body=body)
    if ctx.project is None:
        raise HTTPException(status_code=401, detail="A Project API Key is required")
    ctx.requires("ingest")
    integration = ctx.integration or "custom"
    request.app.state.accounts.touch_integration(project_id=ctx.project.id, kind=integration,
                                                 host=ctx.host, agent=ctx.agent, actions=0)
    session_id = ctx.session or f"{ctx.agent}:{ctx.project.id}"
    if body.get("task"):
        request.app.state.agent_registry.register_task(
            tenant=ctx.project.id, task=str(body["task"])[:200], agent=ctx.agent,
            origin=_origin_for(ctx.project, body.get("origin") or {}))
    return {"session": session_id, "agent": ctx.agent, "project": ctx.project.id,
            "mode": ctx.project.mode}
