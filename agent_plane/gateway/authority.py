"""The task-authority edge (`POST /v1/authorize`) - the core enforcement
primitive: "is this specific proposed action authorised for the current task,
before it reaches the real system?"

Same identity layer and the same signed audit chain as every other edge; this
adds a new decision point on top (:mod:`agent_plane.authority.evaluator`), not
a new trust boundary. `POST /v1/leases` lets an operator (admin-token gated,
like policy hot-reload) issue task-bound grants at runtime; `config/leases.yaml`
seeds the default set the same way `config/tools.yaml` seeds the tool catalog.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from agent_plane.authority.evaluator import evaluate_authority
from agent_plane.authority.lease import parse_lease
from agent_plane.config import Settings
from agent_plane.gateway.authz import require_admin
from agent_plane.gateway.identity import IdentityError, resolve_identity
from agent_plane.schemas.canonical import DecisionAction

authority_router = APIRouter()

_STATUS = {
    DecisionAction.ALLOW: 200,
    DecisionAction.APPROVAL_REQUIRED: 202,
    DecisionAction.DENY: 403,
}


@authority_router.post("/v1/authorize")
async def authorize(
    request: Request,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    audit = request.app.state.audit

    started = time.perf_counter()
    task = (body or {}).get("task")
    action = (body or {}).get("action")
    resource = (body or {}).get("resource")
    if not task or not action or not resource:
        raise HTTPException(status_code=400, detail="'task', 'action', and 'resource' are required")

    try:
        actor = resolve_identity(authorization, settings, request.app.state.revocations)
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    decision = evaluate_authority(
        request.app.state.leases, actor, task=task, action=action, resource=resource
    )

    audit.record({
        "decision_id": decision.decision_id,
        "user_id": actor.user_id,
        "tenant": actor.tenant,
        "department": actor.department,
        "app_id": actor.app_id,
        "agent_id": actor.agent_id,
        "model_requested": f"authorize:{action}",
        "model_used": resource,
        "data_classification": "",
        "decision": decision.decision.value,
        "reason": decision.reason.value,
        "rules_matched": [decision.lease_id] if decision.lease_id else [],
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "prompt_hash": hashlib.sha256(f"{task}:{action}:{resource}".encode()).hexdigest(),
    })
    request.app.state.usage.record({
        "tenant": actor.tenant, "user_id": actor.user_id, "edge": "authorize",
        "resource": action, "units": 1, "calls": 1, "decision_id": decision.decision_id,
    })

    payload = {
        "decision": decision.decision.value,
        "reason": decision.reason.value,
        "lease": decision.lease_id,
        "evidence_id": decision.decision_id,
    }
    status = _STATUS[decision.decision]
    if status != 200:
        raise HTTPException(status_code=status, detail=payload)
    return payload


@authority_router.post("/v1/leases")
async def issue_lease(
    request: Request,
    body: dict[str, Any],
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin(request, x_admin_token)
    try:
        lease = parse_lease(body or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid lease: {exc}") from exc
    request.app.state.leases.add(lease)
    return {"issued": True, "lease": lease.model_dump(mode="json")}


@authority_router.get("/v1/leases/{lease_id}")
async def get_lease(
    request: Request,
    lease_id: str,
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin(request, x_admin_token)
    lease = request.app.state.leases.get(lease_id)
    if lease is None:
        raise HTTPException(status_code=404, detail="lease not found")
    return lease.model_dump(mode="json")
