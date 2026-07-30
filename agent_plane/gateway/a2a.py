"""Agent-to-agent (A2A) edge: scoped delegation hand-offs (`POST /v1/agents/delegate`).

When one agent hands off to another, the child must NOT inherit the parent's full
authority. An authenticated principal asks the control plane to mint a *child*
credential; the control plane enforces **attenuation** - the requested child scope
must be a subset of the parent's verified scope (no privilege escalation) - then
mints a short-lived Ed25519 delegation and records it in the signed audit chain.
The child then acts on every other edge governed by its narrower, verified scope.

Requires ``IDENTITY_MODE=delegation`` (so the parent scope is *verified*, not
self-asserted) and a configured ``DELEGATION_SIGNING_KEY``; otherwise disabled.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import APIRouter, Header, HTTPException, Request

from agent_plane.config import Settings
from agent_plane.gateway.identity import IdentityError, _clearance, resolve_identity
from agent_plane.policy.engine import YamlPolicyEngine
from agent_plane.schemas.canonical import (
    Actor,
    CanonicalAIRequest,
    DataClassification,
    DecisionAction,
    max_classification,
)

a2a_router = APIRouter()


def attenuation_errors(parent: Actor, tools: list[str], clearance: DataClassification,
                       groups: list[str]) -> list[str]:
    """Return reasons the requested child scope exceeds the parent's (empty = OK)."""
    errors: list[str] = []
    extra_tools = [t for t in tools if t not in parent.allowed_tools]
    if extra_tools:
        errors.append(f"tools not held by parent: {extra_tools}")
    if max_classification(parent.clearance, clearance) != parent.clearance:
        errors.append(
            f"clearance exceeds parent ({clearance.value} > {parent.clearance.value})"
        )
    extra_groups = [g for g in groups if g not in parent.groups]
    if extra_groups:
        errors.append(f"groups not held by parent: {extra_groups}")
    return errors


@a2a_router.post("/v1/agents/delegate")
async def delegate(
    request: Request,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine: YamlPolicyEngine = request.app.state.engine
    audit = request.app.state.audit

    # Disabled (hidden) unless the control plane can mint child credentials.
    signing_key = settings.delegation_signing_key_pem
    if not signing_key:
        raise HTTPException(status_code=404, detail="not found")
    if settings.identity_mode != "delegation":
        raise HTTPException(
            status_code=400,
            detail="A2A delegation requires IDENTITY_MODE=delegation (verified parent scope)",
        )

    started = time.perf_counter()
    child_agent = (body or {}).get("agent")
    if not child_agent:
        raise HTTPException(status_code=400, detail="missing 'agent'")
    req_scope = (body or {}).get("scope") or {}
    tools = list(req_scope.get("tools", []))
    groups = list(req_scope.get("groups", []))
    clearance = _clearance(req_scope.get("clearance") or "public")

    # 1. Parent identity (verified delegation + revocation).
    try:
        parent = resolve_identity(authorization, settings, request.app.state.revocations)
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    # 2. Policy can gate A2A delegation by department/etc.
    action = CanonicalAIRequest(
        request_type="delegation",
        model_requested=child_agent,
        data_classification=clearance,
        actor=parent,
    )
    decision = engine.evaluate(action)

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    def record(reason: str | None = None) -> None:
        audit.record({
            "decision_id": decision.decision_id,
            "user_id": parent.user_id,
            "tenant": parent.tenant,
            "department": parent.department,
            "app_id": parent.app_id,
            "agent_id": parent.agent_id,
            "model_requested": f"a2a:{child_agent}",
            "model_used": child_agent,
            "data_classification": clearance.value,
            "decision": decision.decision.value,
            "reason": reason or decision.reason,
            "policy_version": decision.policy_version,
            "rules_matched": decision.rules_matched,
            "latency_ms": elapsed(),
            "prompt_hash": hashlib.sha256(f"{parent.agent_id}->{child_agent}".encode()).hexdigest(),
        })

    if decision.decision == DecisionAction.DENY:
        record()
        raise HTTPException(status_code=403, detail={
            "error": "denied_by_policy", "reason": decision.reason,
            "decision_id": decision.decision_id})
    if decision.decision == DecisionAction.APPROVAL_REQUIRED:
        record()
        raise HTTPException(status_code=202, detail={
            "error": "approval_required", "reason": decision.reason,
            "decision_id": decision.decision_id})

    # 3. Attenuation - the child may never get more than the parent holds.
    errors = attenuation_errors(parent, tools, clearance, groups)
    if errors:
        record(reason="privilege escalation refused: " + "; ".join(errors))
        raise HTTPException(status_code=403, detail={
            "error": "privilege_escalation", "violations": errors,
            "decision_id": decision.decision_id})

    # 4. Mint a short-lived, scoped child delegation (signed by the control plane).
    now = datetime.now(UTC)
    ttl = min(int((body or {}).get("ttl", settings.max_delegation_ttl_seconds)),
              settings.max_delegation_ttl_seconds)
    jti = uuid.uuid4().hex
    child_token = jwt.encode(
        {
            "sub": parent.user_id,           # the human principal is preserved (accountability)
            "app": parent.app_id,
            "agent": child_agent,
            "tenant": parent.tenant,
            "parent_agent": parent.agent_id,  # the delegation chain
            "scope": {"tools": tools, "clearance": clearance.value, "groups": groups},
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=ttl)).timestamp()),
            "jti": jti,
        },
        signing_key,
        algorithm="EdDSA",
    )

    record()
    request.app.state.usage.record({
        "tenant": parent.tenant, "user_id": parent.user_id, "edge": "delegate",
        "resource": child_agent, "units": 1, "calls": 1,
        "decision_id": decision.decision_id,
    })
    return {
        "token": child_token,
        "agent": child_agent,
        "scope": {"tools": tools, "clearance": clearance.value, "groups": groups},
        "jti": jti,
        "expires_in": ttl,
        "x_control_plane": {
            "decision_id": decision.decision_id,
            "parent_agent": parent.agent_id,
            "policy_version": decision.policy_version,
        },
    }
