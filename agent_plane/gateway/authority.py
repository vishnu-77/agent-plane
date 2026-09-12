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

from agent_plane.authority.lease import AuthorityLease, lease_attenuation_errors, parse_lease
from agent_plane.authority.provenance import validate_context
from agent_plane.gateway.authz import require_admin
from agent_plane.gateway.context import resolve_request

authority_router = APIRouter()


@authority_router.post("/v1/authorize")
async def authorize(
    request: Request,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """One governed decision. The full chain (identity -> task -> authority
    lineage -> action -> resource -> consequence -> decision -> explanation) is
    computed by :class:`agent_plane.authority.service.AuthorityService`,
    recorded on the audit chain, and readable at ``GET /v1/decisions/{id}``."""
    body = body or {}
    task = body.get("task")
    action = body.get("action")
    resource = body.get("resource")
    if not task or not action or not resource:
        raise HTTPException(status_code=400, detail="'task', 'action', and 'resource' are required")
    impact = body.get("impact") or "reversible"
    if impact not in ("reversible", "irreversible"):
        raise HTTPException(status_code=400, detail="'impact' must be reversible or irreversible")
    approval_id = body.get("approval")
    if approval_id is not None and (not isinstance(approval_id, str) or not approval_id):
        raise HTTPException(status_code=400, detail="'approval' must be an approval id")
    try:
        context = validate_context(body.get("context"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # A Project API Key, or one of the token identity modes. Either way the
    # caller ends up as an Actor in exactly one project.
    ctx = resolve_request(request, authorization=authorization, x_api_key=x_api_key, body=body)
    ctx.requires("authorize")

    result = request.app.state.authority.decide(
        ctx.actor, task=task, action=action, resource=resource, impact=impact,
        # The SDKs declare an integration on every request, and "custom" is what
        # they say when the caller named none. That is not a distinct edge: this
        # one is the decision API, so only a named integration renames it.
        approval=approval_id, context=context,
        edge=ctx.integration if ctx.integration and ctx.integration != "custom" else "authorize",
        integration=ctx.integration,
    )
    if result.http_status != 200:
        raise HTTPException(status_code=result.http_status, detail=result.payload)
    return result.payload


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
    if not lease.origin:
        lease = lease.model_copy(update={"origin": {"kind": "api", "created_by": "admin"}})
    request.app.state.leases.add(lease)
    _attach(request, lease)
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
    origin = body.get("origin") if isinstance(body.get("origin"), dict) else {"kind": "api", "created_by": "admin"}
    lease = lease.model_copy(update={"origin": {**origin, "template": name}})
    request.app.state.leases.add(lease)
    _attach(request, lease)
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
    audit = request.app.state.audit
    store = request.app.state.leases

    started = time.perf_counter()
    parent = store.get(lease_id)
    if parent is None:
        raise HTTPException(status_code=404, detail="lease not found")

    actor = resolve_request(request, authorization=authorization, body=body or {}).actor

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
            parent_lease=parent.id,
            origin={"kind": "parent", "ref": parent.id, "created_by": parent.subject},
            permitted_consequence=dict((body or {}).get("permitted_consequence") or parent.permitted_consequence),
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
    _attach(request, child, parent_agent=parent.subject)
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
                "require_approval", "expires_at", "maximum_impact", "permitted_consequence",
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


def _attach(request: Request, lease: AuthorityLease, parent_agent: str | None = None) -> None:
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is not None:
        registry.attach_lease(tenant=lease.tenant, task=lease.task, agent=lease.subject,
                              lease_id=lease.id, parent_agent=parent_agent)


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
