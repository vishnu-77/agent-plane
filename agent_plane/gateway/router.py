"""The AI Gateway data plane.

Exposes an OpenAI-compatible surface and runs every request through the governed
flow: identity -> normalize -> quota -> policy -> guardrails -> route -> filter
-> audit. Shared components are read from ``request.app.state`` (wired in
``main.py``).
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from agent_plane.config import Settings
from agent_plane.gateway.authz import require_admin
from agent_plane.gateway.identity import IdentityError, resolve_identity
from agent_plane.gateway.normalizer import normalize
from agent_plane.guardrails import scanner
from agent_plane.policy.engine import YamlPolicyEngine
from agent_plane.routing.providers import ProviderError
from agent_plane.routing.registry import ModelRegistry
from agent_plane.schemas.canonical import Actor, CanonicalAIRequest, Decision, DecisionAction
from agent_plane.schemas.openai import ChatCompletionRequest, ModelInfo, ModelList

router = APIRouter()


def _prompt_hash(messages: list[dict[str, Any]]) -> str:
    joined = "\n".join(str(m.get("content") or "") for m in messages)
    return hashlib.sha256(joined.encode()).hexdigest()


def _audit_event(
    actor: Actor,
    canonical: CanonicalAIRequest,
    decision: Decision,
    *,
    model_used: str | None,
    total_tokens: int,
    latency_ms: int,
    redactions: list[str],
) -> dict[str, Any]:
    full_log = decision.log_level.value == "full"
    return {
        "decision_id": decision.decision_id,
        "user_id": actor.user_id,
        "tenant": actor.tenant,
        "department": actor.department,
        "app_id": actor.app_id,
        "agent_id": actor.agent_id,
        "model_requested": canonical.model_requested,
        "model_used": model_used,
        "data_classification": canonical.data_classification.value,
        "decision": decision.decision.value,
        "reason": decision.reason,
        "policy_version": decision.policy_version,
        "rules_matched": decision.rules_matched,
        "obligations_applied": decision.obligations,
        "redactions_applied": redactions,
        "log_level": decision.log_level.value,
        "estimated_tokens": canonical.estimated_tokens,
        "total_tokens": total_tokens,
        "latency_ms": latency_ms,
        # metadata_only mode keeps a hash, never the raw prompt.
        "prompt_hash": _prompt_hash(canonical.messages) if not full_log else None,
    }


@router.get("/v1/models", response_model=ModelList)
async def list_models(request: Request) -> ModelList:
    registry: ModelRegistry = request.app.state.registry
    return ModelList(data=[ModelInfo(id=m) for m in registry.list_models()])


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, Any]:
    engine: YamlPolicyEngine = request.app.state.engine
    return {"status": "ok", "policy_version": engine.bundle.version}


@router.get("/v1/audit")
async def audit_log(
    request: Request,
    limit: int = 50,
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    # Audit is operator evidence (cross-tenant, with the signed chain): admin-only.
    require_admin(request, x_admin_token)
    store = request.app.state.audit
    return {"events": store.recent(limit=limit)}


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    body: ChatCompletionRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine: YamlPolicyEngine = request.app.state.engine
    registry: ModelRegistry = request.app.state.registry
    cache = request.app.state.cache
    audit = request.app.state.audit

    started = time.perf_counter()

    # 1. Identity
    try:
        actor = resolve_identity(
            authorization, settings, request.app.state.revocations
        )
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    # 2. Normalize
    canonical = normalize(body, actor)

    # 3. Policy decision
    decision = engine.evaluate(canonical)

    def elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    # 4. Deny / approval short-circuits (still audited)
    if decision.decision == DecisionAction.DENY:
        audit.record(
            _audit_event(
                actor, canonical, decision,
                model_used=None, total_tokens=0,
                latency_ms=elapsed_ms(), redactions=[],
            )
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "denied_by_policy",
                "reason": decision.reason,
                "decision_id": decision.decision_id,
                "policy_version": decision.policy_version,
                "rules_matched": decision.rules_matched,
                "denied_tools": decision.denied_tools,
            },
        )

    if decision.decision == DecisionAction.APPROVAL_REQUIRED:
        audit.record(
            _audit_event(
                actor, canonical, decision,
                model_used=None, total_tokens=0,
                latency_ms=elapsed_ms(), redactions=[],
            )
        )
        raise HTTPException(
            status_code=202,
            detail={
                "error": "approval_required",
                "reason": decision.reason,
                "decision_id": decision.decision_id,
            },
        )

    # 5. Token quota (obligation-gated)
    if "enforce_token_quota" in decision.obligations:
        qkey = f"{actor.tenant}:{actor.user_id}"
        used = cache.incr_quota(
            qkey, canonical.estimated_tokens, settings.quota_window_seconds
        )
        if used > settings.default_token_quota:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "token_quota_exceeded",
                    "used": used,
                    "limit": settings.default_token_quota,
                    "decision_id": decision.decision_id,
                },
            )

    # 6. Request-side redaction (obligation-gated)
    forwarded = body.passthrough_body()
    request_redactions: list[str] = []
    if decision.redact:
        forwarded["messages"], request_redactions = scanner.redact_messages(
            forwarded.get("messages", []), decision.redact
        )

    # Enforce policy-imposed max_tokens ceiling.
    if decision.max_tokens is not None:
        requested = forwarded.get("max_tokens")
        forwarded["max_tokens"] = (
            decision.max_tokens if requested is None else min(requested, decision.max_tokens)
        )

    # 7. Route to model (with fallback)
    route_model = decision.route or canonical.model_requested
    try:
        response, model_used = await registry.route(route_model, forwarded)
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderError as exc:
        audit.record(
            _audit_event(
                actor, canonical, decision,
                model_used=None, total_tokens=0,
                latency_ms=elapsed_ms(), redactions=request_redactions,
            )
        )
        raise HTTPException(status_code=502, detail=f"upstream_error: {exc}") from exc

    # 8. Response-side redaction
    response_redactions: list[str] = []
    if decision.redact:
        for choice in response.get("choices", []):
            msg = choice.get("message", {})
            content = msg.get("content")
            if isinstance(content, str):
                msg["content"], hits = scanner.redact(content, decision.redact)
                for h in hits:
                    if h not in response_redactions:
                        response_redactions.append(h)

    # 8b. Mediate the *action*: strip any tool call the actor isn't permitted to
    # make, even one the model improvised. Least privilege applies to outputs too.
    tool_calls_blocked: list[str] = []
    if actor.allowed_tools:
        permitted = set(actor.allowed_tools)
        for choice in response.get("choices", []):
            msg = choice.get("message", {})
            calls = msg.get("tool_calls")
            if isinstance(calls, list):
                kept = []
                for call in calls:
                    name = (call.get("function") or {}).get("name")
                    if name and name not in permitted:
                        tool_calls_blocked.append(name)
                    else:
                        kept.append(call)
                msg["tool_calls"] = kept

    # 9. Audit + meter
    total_tokens = (response.get("usage") or {}).get("total_tokens", 0)
    all_redactions = request_redactions + [
        r for r in response_redactions if r not in request_redactions
    ]
    audit.record(
        _audit_event(
            actor, canonical, decision,
            model_used=model_used, total_tokens=total_tokens,
            latency_ms=elapsed_ms(), redactions=all_redactions,
        )
    )
    request.app.state.usage.record(
        {
            "tenant": actor.tenant,
            "user_id": actor.user_id,
            "edge": "model",
            "resource": model_used or canonical.model_requested,
            "units": total_tokens,
            "calls": 1,
            "decision_id": decision.decision_id,
        }
    )

    # Surface control-plane metadata to the caller (non-standard but useful).
    response["x_control_plane"] = {
        "decision_id": decision.decision_id,
        "policy_version": decision.policy_version,
        "rules_matched": decision.rules_matched,
        "obligations_applied": decision.obligations,
        "redactions_applied": all_redactions,
        "tool_calls_blocked": tool_calls_blocked,
        "model_used": model_used,
    }
    return response
