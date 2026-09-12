"""The task-authority edge (`POST /v1/authorize`) - the core *decision*
primitive: "is this specific proposed action authorised for the current task,
before it reaches the real system?"

This edge executes nothing: it answers, and the caller acts on the answer, so
an agent that never asks is not bound by it. The enforcing edges are the tool
broker, the model proxy, and the MCP gateway, where the credential lives
server-side. See "Where a decision actually binds" in README.md.

Same identity layer and the same signed audit chain as every other edge; this
adds a new decision point on top (:mod:`agent_plane.authority.evaluator`), not
a new trust boundary.

Lease lifecycle (all admin-token gated except delegation, which is
self-service for the lease holder):

    POST   /v1/leases                     issue a lease from a full document
    POST   /v1/leases/from-template       issue from a named template + variables
    GET    /v1/lease-templates            list configured templates
    GET    /v1/leases/{id}
    PATCH  /v1/leases/{id}                shrink (never widen)
    DELETE /v1/leases/{id}                revoke, effective immediately
    POST   /v1/leases/{id}/delegate       holder mints an attenuated child

Approval resume: an APPROVAL_REQUIRED decision returns an ``approval_id``.
Once an operator approves it (``/v1/approvals``), the executor repeats the
same authorize call with ``"approval": "<id>"`` and receives ALLOW /
ACTION_APPROVED exactly once.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError

from agent_plane.approvals.store import new_request
from agent_plane.authority.evaluator import (
    AuthorityDecision,
    AuthorityReason,
    evaluate_authority,
)
from agent_plane.authority.lease import AuthorityLease, lease_attenuation_errors, parse_lease
from agent_plane.authority.provenance import provenance_record, validate_context
from agent_plane.config import Settings
from agent_plane.gateway.authz import require_admin
from agent_plane.gateway.identity import IdentityError, resolve_identity
from agent_plane.schemas.canonical import Actor, DecisionAction

authority_router = APIRouter()

_STATUS = {
    DecisionAction.ALLOW: 200,
    DecisionAction.APPROVAL_REQUIRED: 202,
    DecisionAction.DENY: 403,
}


def _resume_from_approval(
    request: Request, actor: Actor, approval_id: str, *, task: str, action: str, resource: str,
    impact: str,
) -> tuple[AuthorityDecision, str | None]:
    """Turn an approved request into a one-shot ALLOW, re-checking the lease first."""
    store = request.app.state.approvals
    decision_id = f"az_{uuid.uuid4().hex[:12]}"
    req = store.get(approval_id)
    subject = actor.agent_id or actor.user_id
    if req is None or req.subject != subject or req.tenant != actor.tenant:
        return AuthorityDecision(decision=DecisionAction.DENY,
                                 reason=AuthorityReason.APPROVAL_NOT_FOUND,
                                 decision_id=decision_id), None
    if (req.task, req.action, req.resource) != (task, action, resource):
        return AuthorityDecision(decision=DecisionAction.DENY,
                                 reason=AuthorityReason.APPROVAL_MISMATCH,
                                 lease_id=req.lease_id, decision_id=decision_id), req.id
    if req.status == "pending":
        return AuthorityDecision(decision=DecisionAction.APPROVAL_REQUIRED,
                                 reason=AuthorityReason.APPROVAL_PENDING,
                                 lease_id=req.lease_id, decision_id=decision_id), req.id
    if req.status != "approved":
        reason = {
            "rejected": AuthorityReason.APPROVAL_REJECTED,
            "expired": AuthorityReason.APPROVAL_EXPIRED,
            "consumed": AuthorityReason.APPROVAL_ALREADY_USED,
        }[req.status]
        return AuthorityDecision(decision=DecisionAction.DENY, reason=reason,
                                 lease_id=req.lease_id, decision_id=decision_id), req.id
    # Approved: the lease must still be live (a revocation or expiry wins), and
    # the approval is spent atomically so it cannot authorise two executions.
    with request.app.state.leases.transaction():
        current = evaluate_authority(
            request.app.state.leases, actor, task=task, action=action, resource=resource,
            impact=impact, consume=False,
            lease_ids=frozenset([req.lease_id]) if req.lease_id else None,
        )
        if current.decision == DecisionAction.DENY:
            return current, req.id
        if not store.consume(req.id):
            return AuthorityDecision(decision=DecisionAction.DENY,
                                     reason=AuthorityReason.APPROVAL_ALREADY_USED,
                                     lease_id=req.lease_id, decision_id=decision_id), req.id
    return AuthorityDecision(decision=DecisionAction.ALLOW, reason=AuthorityReason.ACTION_APPROVED,
                             lease_id=req.lease_id, decision_id=decision_id), req.id


@authority_router.post("/v1/authorize")
async def authorize(
    request: Request,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    audit = request.app.state.audit

    started = time.perf_counter()
    body = body or {}
    task = body.get("task")
    action = body.get("action")
    resource = body.get("resource")
    if not task or not action or not resource:
        raise HTTPException(status_code=400, detail="'task', 'action', and 'resource' are required")
    # Caller-declared, like action/resource - the evaluator enforces it against
    # the lease's `maximum_impact` ceiling, it doesn't independently verify it.
    impact = body.get("impact") or "reversible"
    approval_id = body.get("approval")
    if approval_id is not None and (not isinstance(approval_id, str) or not approval_id):
        raise HTTPException(status_code=400, detail="'approval' must be an approval id")
    try:
        context = validate_context(body.get("context"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        actor = resolve_identity(authorization, settings, request.app.state.revocations)
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    if approval_id:
        decision, approval_ref = _resume_from_approval(
            request, actor, approval_id, task=task, action=action, resource=resource,
            impact=impact)
    else:
        decision = evaluate_authority(
            request.app.state.leases, actor, task=task, action=action, resource=resource,
            impact=impact,
        )
        approval_ref = None
        if decision.decision == DecisionAction.APPROVAL_REQUIRED:
            lease = request.app.state.leases.get(decision.lease_id) if decision.lease_id else None
            req = new_request(
                tenant=actor.tenant, subject=actor.agent_id or actor.user_id, task=task,
                action=action, resource=resource, lease_id=decision.lease_id,
                evidence_id=decision.decision_id, ttl_seconds=settings.approval_ttl_seconds,
                context=context, lease_expires_at=lease.expires_at if lease else None,
            )
            request.app.state.approvals.create(req)
            approval_ref = req.id

    obligations: list[Any] = []
    if context:
        obligations.append(provenance_record(context))
    if approval_ref:
        obligations.append({"schema": "agent-plane.approval.v1", "approval_id": approval_ref})

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
        "obligations_applied": obligations,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "prompt_hash": hashlib.sha256(f"{task}:{action}:{resource}".encode()).hexdigest(),
    })
    request.app.state.usage.record({
        "tenant": actor.tenant, "user_id": actor.user_id, "edge": "authorize",
        "resource": action, "units": 1, "calls": 1, "decision_id": decision.decision_id,
    })
    request.app.state.metrics.observe_decision("authorize", decision.decision.value, decision.reason.value)

    if approval_ref and not approval_id and decision.decision == DecisionAction.APPROVAL_REQUIRED:
        req = request.app.state.approvals.get(approval_ref)
        if req is not None:
            request.app.state.approval_notifier.emit("approval.requested", req.model_dump(mode="json"))

    payload: dict[str, Any] = {
        "decision": decision.decision.value,
        "reason": decision.reason.value,
        "lease": decision.lease_id,
        "evidence_id": decision.decision_id,
    }
    if approval_ref:
        payload["approval_id"] = approval_ref
    if context:
        payload["context"] = context
    status = _STATUS[decision.decision]
    if status != 200:
        raise HTTPException(status_code=status, detail=payload)
    return payload


# --------------------------------------------------------------------------- #
# Lease issuance
# --------------------------------------------------------------------------- #
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
    _audit_admin(request, decision="allow", reason="LEASE_ISSUED", lease_id=lease.id)
    return {"issued": True, "lease": lease.model_dump(mode="json")}


@authority_router.get("/v1/lease-templates")
async def list_lease_templates(
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin(request, x_admin_token)
    templates = request.app.state.lease_templates.list()
    return {"templates": [
        {**t.model_dump(mode="json"), "variables": t.variables} for t in templates
    ]}


@authority_router.post("/v1/leases/from-template")
async def issue_lease_from_template(
    request: Request,
    body: dict[str, Any],
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin(request, x_admin_token)
    body = body or {}
    name, subject, task = body.get("template"), body.get("subject"), body.get("task")
    if not name or not subject or not task:
        raise HTTPException(status_code=400, detail="'template', 'subject', and 'task' are required")
    template = request.app.state.lease_templates.get(name)
    if template is None:
        raise HTTPException(status_code=404, detail="unknown lease template")
    variables = body.get("variables") or {}
    if not isinstance(variables, dict):
        raise HTTPException(status_code=400, detail="'variables' must be an object")
    lease_id = body.get("id")
    if lease_id is not None and (not isinstance(lease_id, str) or not lease_id):
        raise HTTPException(status_code=400, detail="'id' must be a non-empty string")
    tenant = body.get("tenant") or "default"
    if not isinstance(tenant, str) or not tenant:
        raise HTTPException(status_code=400, detail="'tenant' must be a non-empty string")
    try:
        lease = template.render(subject=subject, task=task, tenant=tenant,
                                variables=variables, lease_id=lease_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid template variables: {exc}") from exc
    if lease_id and request.app.state.leases.get(lease_id) is not None:
        raise HTTPException(status_code=409, detail="lease id already exists")
    request.app.state.leases.add(lease)
    _audit_admin(request, decision="allow", reason=f"LEASE_ISSUED_FROM_TEMPLATE:{name}",
                 lease_id=lease.id)
    return {"issued": True, "template": name, "lease": lease.model_dump(mode="json")}


@authority_router.post("/v1/leases/{lease_id}/delegate")
async def delegate_lease(
    request: Request,
    lease_id: str,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """The lease holder mints an attenuated child lease (self-service, like
    `POST /v1/agents/delegate` - not admin-gated). Child scope must be a
    subset of the parent's; the parent must allow delegation at all
    (`child_authority != "none"`)."""
    settings: Settings = request.app.state.settings
    audit = request.app.state.audit
    store = request.app.state.leases

    started = time.perf_counter()
    parent = store.get(lease_id)
    if parent is None:
        raise HTTPException(status_code=404, detail="lease not found")

    try:
        actor = resolve_identity(authorization, settings, request.app.state.revocations)
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    if actor.agent_id != parent.subject:
        raise HTTPException(status_code=403, detail="only the lease holder may delegate it")
    if parent.child_authority == "none":
        raise HTTPException(
            status_code=403,
            detail="parent lease forbids delegation (child_authority=none)",
        )

    child_agent = (body or {}).get("agent")
    if not child_agent:
        raise HTTPException(status_code=400, detail="missing 'agent' (the child lease's subject)")

    try:
        child = AuthorityLease(
            id=(body or {}).get("id") or f"lease-{uuid.uuid4().hex[:12]}",
            task=parent.task,
            subject=child_agent,
            tenant=parent.tenant,  # delegation never changes tenant
            resources=list((body or {}).get("resources") or parent.resources),
            actions=list((body or {}).get("actions") or parent.actions),
            protected_resources=list(
                set(parent.protected_resources) | set((body or {}).get("protected_resources") or [])
            ),
            max_uses=(body or {}).get("max_uses") or dict(parent.max_uses),
            require_approval=list((body or {}).get("require_approval") or parent.require_approval),
            expires_at=(body or {}).get("expires_at") or parent.expires_at,
            maximum_impact=(body or {}).get("maximum_impact") or parent.maximum_impact,
            # A child cannot re-delegate unless explicitly granted - default "none".
            child_authority=(body or {}).get("child_authority") or "none",
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=400, detail=f"invalid child lease: {exc.error_count()} error(s)"
        ) from exc

    decision_id = f"az_{uuid.uuid4().hex[:12]}"
    errors = lease_attenuation_errors(parent, child)

    def record(decision: str, reason: str) -> None:
        audit.record({
            "decision_id": decision_id,
            "user_id": actor.user_id,
            "tenant": actor.tenant,
            "department": actor.department,
            "app_id": actor.app_id,
            "agent_id": actor.agent_id,
            "model_requested": f"lease-delegate:{lease_id}",
            "model_used": child.id,
            "data_classification": "",
            "decision": decision,
            "reason": reason,
            "rules_matched": [lease_id],
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "prompt_hash": hashlib.sha256(f"{lease_id}->{child_agent}".encode()).hexdigest(),
        })

    if errors:
        record("deny", "privilege escalation refused: " + "; ".join(errors))
        raise HTTPException(status_code=403, detail={
            "error": "privilege_escalation", "violations": errors,
            "decision_id": decision_id})

    store.add(child)
    record("allow", "ACTION_WITHIN_TASK_AUTHORITY")
    request.app.state.usage.record({
        "tenant": actor.tenant, "user_id": actor.user_id, "edge": "lease-delegate",
        "resource": child.id, "units": 1, "calls": 1, "decision_id": decision_id,
    })
    return {
        "issued": True,
        "lease": child.model_dump(mode="json"),
        "x_control_plane": {"decision_id": decision_id, "parent_lease": parent.id},
    }


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


@authority_router.delete("/v1/leases/{lease_id}")
async def revoke_lease(
    request: Request,
    lease_id: str,
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Revoke a lease outright, effective immediately. Kept in the store, not
    deleted, so `GET /v1/leases/{id}` still shows it for audit."""
    require_admin(request, x_admin_token)
    if not request.app.state.leases.revoke(lease_id):
        raise HTTPException(status_code=404, detail="lease not found")
    _audit_admin(request, decision="deny", reason="LEASE_REVOKED", lease_id=lease_id)
    return {"revoked": True, "lease": lease_id}


@authority_router.patch("/v1/leases/{lease_id}")
async def shrink_lease(
    request: Request,
    lease_id: str,
    body: dict[str, Any],
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Narrow an active lease's authority in place. Only a subset of the
    lease's current resources/actions/max_uses/maximum_impact/expiry - same
    "never grants more" rule as delegation; widening is refused."""
    require_admin(request, x_admin_token)
    store = request.app.state.leases
    with store.transaction():
        current = store.get(lease_id)
        if current is None:
            raise HTTPException(status_code=404, detail="lease not found")

        body = body or {}
        # model_copy(update=...) does NOT validate in pydantic v2 - a string
        # `expires_at` would be stored in a datetime field and then raise
        # TypeError in the evaluator on every later authorize call for this
        # subject+task. Re-validate the merged document instead.
        merged = current.model_dump(mode="json")
        merged.update({
            k: body[k] for k in (
                "resources", "actions", "protected_resources", "max_uses",
                "require_approval", "expires_at", "maximum_impact",
            ) if k in body
        })
        try:
            shrunk = AuthorityLease.model_validate(merged)
        except ValidationError as exc:
            raise HTTPException(
                status_code=400, detail=f"invalid lease update: {exc.error_count()} error(s)"
            ) from exc

        errors = lease_attenuation_errors(current, shrunk)
        if errors:
            _audit_admin(
                request, decision="deny",
                reason="privilege escalation refused: " + "; ".join(errors), lease_id=lease_id,
            )
            raise HTTPException(status_code=403, detail={
                "error": "privilege_escalation", "violations": errors})

        store.add(shrunk)
        _audit_admin(request, decision="allow", reason="LEASE_SHRUNK", lease_id=lease_id)
        return {"shrunk": True, "lease": shrunk.model_dump(mode="json")}


def _audit_admin(request: Request, *, decision: str, reason: str, lease_id: str) -> None:
    request.app.state.audit.record({
        "decision_id": f"az_{uuid.uuid4().hex[:12]}",
        "user_id": "admin",
        "tenant": "-",
        "department": None,
        "app_id": None,
        "agent_id": None,
        "model_requested": f"lease-admin:{lease_id}",
        "model_used": lease_id,
        "data_classification": "",
        "decision": decision,
        "reason": reason,
        "rules_matched": [lease_id],
        "latency_ms": 0,
        "prompt_hash": hashlib.sha256(lease_id.encode()).hexdigest(),
    })
