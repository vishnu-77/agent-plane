"""Approval API: the human-in-the-loop half of APPROVAL_REQUIRED.

    GET  /v1/approvals?status=pending          admin: queue of open requests
    GET  /v1/approvals/{id}                    admin, or the agent it was raised for
    POST /v1/approvals/{id}/approve  {note}    admin
    POST /v1/approvals/{id}/reject   {note}    admin

An approved request is resumed by the executor with
``POST /v1/authorize {"task","action","resource","approval": "<id>"}`` which
returns ALLOW / ACTION_APPROVED exactly once. Every decision here lands on
the signed audit chain and, when configured, on the approval webhook.
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request

from agent_plane.approvals.store import ApprovalRequest
from agent_plane.gateway.authz import require_admin, resolve_operator
from agent_plane.gateway.identity import IdentityError, resolve_identity

approvals_router = APIRouter(tags=["approvals"])


def _is_admin(request: Request, x_admin_token: str | None) -> bool:
    settings = request.app.state.settings
    return bool(settings.admin_token) and hmac.compare_digest(
        x_admin_token or "", settings.admin_token
    )


def _audit(request: Request, req: ApprovalRequest, *, decision: str, reason: str,
           decided_by: str) -> str:
    decision_id = f"az_{uuid.uuid4().hex[:12]}"
    request.app.state.audit.record({
        "decision_id": decision_id,
        "user_id": decided_by,
        "tenant": req.tenant,
        "department": None,
        "app_id": None,
        "agent_id": req.subject,
        "model_requested": f"approval:{req.action}",
        "model_used": req.resource,
        "data_classification": "",
        "decision": decision,
        "reason": reason,
        "rules_matched": [req.lease_id] if req.lease_id else [],
        "obligations_applied": [{
            "schema": "agent-plane.approval.v1", "approval_id": req.id,
            "evidence_id": req.evidence_id, "status": req.status, "note": req.note,
        }],
        "latency_ms": 0,
        "prompt_hash": hashlib.sha256(f"{req.task}:{req.action}:{req.resource}".encode()).hexdigest(),
    })
    return decision_id


@approvals_router.get("/v1/approvals")
async def list_approvals(
    request: Request,
    status: str | None = Query(default="pending"),
    tenant: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    x_admin_token: str | None = Header(default=None),
    x_demo_token: str | None = Header(default=None),
) -> dict[str, Any]:
    # Operators see every tenant; the demo viewer sees the demo tenant only.
    scope = resolve_operator(request, x_admin_token, x_demo_token)
    tenant = scope.restrict(tenant)
    if status == "all":
        status = None
    items = request.app.state.approvals.list(status=status, tenant=tenant, limit=limit)
    return {"approvals": [item.model_dump(mode="json") for item in items], "count": len(items)}


@approvals_router.get("/v1/approvals/{approval_id}")
async def get_approval(
    request: Request,
    approval_id: str,
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    req = request.app.state.approvals.get(approval_id)
    if _is_admin(request, x_admin_token):
        if req is None:
            raise HTTPException(status_code=404, detail="approval not found")
        return req.model_dump(mode="json")
    # Otherwise the agent (via its executor) may poll its own request.
    try:
        actor = resolve_identity(authorization, request.app.state.settings,
                                 request.app.state.revocations)
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    subject = actor.agent_id or actor.user_id
    if req is None or req.subject != subject or req.tenant != actor.tenant:
        raise HTTPException(status_code=404, detail="approval not found")
    return req.model_dump(mode="json")


def _decide(request: Request, approval_id: str, body: dict[str, Any] | None,
            *, status: str, x_admin_token: str | None) -> dict[str, Any]:
    require_admin(request, x_admin_token)
    store = request.app.state.approvals
    current = store.get(approval_id)
    if current is None:
        raise HTTPException(status_code=404, detail="approval not found")
    if current.status != "pending":
        raise HTTPException(status_code=409, detail={
            "error": "approval_not_pending", "status": current.status})
    note = (body or {}).get("note")
    if note is not None and (not isinstance(note, str) or len(note) > 1000):
        raise HTTPException(status_code=400, detail="'note' must be a string of at most 1000 chars")
    decided_by = (body or {}).get("decided_by") or "admin"
    if not isinstance(decided_by, str) or len(decided_by) > 128:
        raise HTTPException(status_code=400, detail="'decided_by' must be a short string")
    decided = store.decide(approval_id, status=status, decided_by=decided_by, note=note)
    if decided is None or decided.status != status:
        raise HTTPException(status_code=409, detail={
            "error": "approval_not_pending", "status": decided.status if decided else "missing"})
    decision_id = _audit(
        request, decided,
        decision="allow" if status == "approved" else "deny",
        reason="APPROVAL_GRANTED" if status == "approved" else "APPROVAL_REJECTED",
        decided_by=decided_by,
    )
    request.app.state.approval_notifier.emit(f"approval.{status}", decided.model_dump(mode="json"))
    return {"approval": decided.model_dump(mode="json"), "evidence_id": decision_id}


@approvals_router.post("/v1/approvals/{approval_id}/approve")
async def approve(
    request: Request,
    approval_id: str,
    body: dict[str, Any] | None = None,
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    return _decide(request, approval_id, body, status="approved", x_admin_token=x_admin_token)


@approvals_router.post("/v1/approvals/{approval_id}/reject")
async def reject(
    request: Request,
    approval_id: str,
    body: dict[str, Any] | None = None,
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    return _decide(request, approval_id, body, status="rejected", x_admin_token=x_admin_token)
