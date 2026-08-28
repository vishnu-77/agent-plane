"""The RAG authorization edge: identity-aware retrieval (`POST /v1/retrieve`).

Reuses the same control plane as the model and tool edges: identity (verified
delegation + revocation), the deterministic engine for *source-level* decisions,
and the one signed audit chain. The RAG-specific part is the *document-level*
authorization filter - relevance is not permission, enforced before generation.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from agent_plane.config import Settings
from agent_plane.gateway.identity import IdentityError, resolve_identity
from agent_plane.guardrails import scanner
from agent_plane.policy.engine import YamlPolicyEngine
from agent_plane.routing.knowledge import KnowledgeStore
from agent_plane.schemas.canonical import (
    Actor,
    CanonicalAIRequest,
    DataClassification,
    Decision,
    DecisionAction,
)

retrieval_router = APIRouter()


def _audit_event(actor: Actor, source: str, decision: Decision, *, query: str,
                 latency_ms: int, redactions: list[str]) -> dict[str, Any]:
    return {
        "decision_id": decision.decision_id,
        "user_id": actor.user_id,
        "tenant": actor.tenant,
        "department": actor.department,
        "app_id": actor.app_id,
        "agent_id": actor.agent_id,
        "model_requested": f"rag:{source}",
        "model_used": source,
        "data_classification": DataClassification.PUBLIC.value,
        "decision": decision.decision.value,
        "reason": decision.reason,
        "policy_version": decision.policy_version,
        "rules_matched": decision.rules_matched,
        "obligations_applied": decision.obligations,
        "redactions_applied": redactions,
        "latency_ms": latency_ms,
        "prompt_hash": hashlib.sha256(query.encode()).hexdigest(),
    }


@retrieval_router.post("/v1/retrieve")
async def retrieve(
    request: Request,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine: YamlPolicyEngine = request.app.state.engine
    knowledge: KnowledgeStore = request.app.state.knowledge
    audit = request.app.state.audit

    started = time.perf_counter()
    source = (body or {}).get("source")
    query = (body or {}).get("query") or ""
    top_k = int((body or {}).get("top_k", 5))
    if not source:
        raise HTTPException(status_code=400, detail="missing 'source'")
    if not query:
        raise HTTPException(status_code=400, detail="missing 'query'")

    # Default-deny: unknown sources are never queried.
    if knowledge.get(source) is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_source", "source": source})

    # 1. Identity.
    try:
        actor = resolve_identity(authorization, settings, request.app.state.revocations)
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    # 2. Source-level decision (the deterministic engine can gate a whole source).
    action = CanonicalAIRequest(
        request_type="retrieval",
        model_requested=source,
        data_classification=DataClassification.PUBLIC,
        actor=actor,
    )
    decision = engine.evaluate(action)

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    def record(redactions: list[str] | None = None) -> None:
        audit.record(_audit_event(
            actor, source, decision, query=query, latency_ms=elapsed(),
            redactions=redactions or [],
        ))

    if decision.decision == DecisionAction.DENY:
        record()
        raise HTTPException(
            status_code=403,
            detail={"error": "denied_by_policy", "reason": decision.reason,
                    "decision_id": decision.decision_id},
        )
    if decision.decision == DecisionAction.APPROVAL_REQUIRED:
        record()
        raise HTTPException(
            status_code=202,
            detail={"error": "approval_required", "reason": decision.reason,
                    "decision_id": decision.decision_id},
        )

    # 3. Retrieve + authorize at the document level (relevance is not permission).
    results, retrieved_n, filtered_n = knowledge.retrieve(source, query, actor, top_k)

    # 3b. Redaction obligation: a matching policy can require PII/secrets scrubbed
    # from document text before it reaches the caller, same as the model edge.
    redactions: list[str] = []
    if decision.redact:
        results, redactions = scanner.redact_json(results, decision.redact)

    record(redactions)
    request.app.state.usage.record(
        {
            "tenant": actor.tenant,
            "user_id": actor.user_id,
            "edge": "retrieve",
            "resource": source,
            "units": len(results),
            "calls": 1,
            "decision_id": decision.decision_id,
        }
    )
    return {
        "results": results,
        "x_control_plane": {
            "decision_id": decision.decision_id,
            "policy_version": decision.policy_version,
            "source": source,
            "retrieved": retrieved_n,
            "returned": len(results),
            "redactions_applied": redactions,
            "filtered_by_authorization": filtered_n,
        },
    }
