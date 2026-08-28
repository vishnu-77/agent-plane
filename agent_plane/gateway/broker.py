"""The agent->tool broker edge.

A second enforcement point that reuses the *same* control plane as the model
edge: identity (verified delegation + revocation), the deterministic policy
engine (least-privilege ``allowed_tools`` + per-tool approval), and the one
hash-chained, signed audit log. The agent requests an action; the broker decides,
executes with its own credential, and records the decision - never the agent.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from agent_plane.config import Settings
from agent_plane.gateway.identity import IdentityError, resolve_identity
from agent_plane.guardrails import scanner
from agent_plane.guardrails.classifier import derive_classification
from agent_plane.policy.engine import YamlPolicyEngine
from agent_plane.routing.tools import ToolExecutionError, ToolRegistry
from agent_plane.schemas.canonical import (
    Actor,
    CanonicalAIRequest,
    Decision,
    DecisionAction,
)

broker_router = APIRouter()


def _args_text(arguments: dict[str, Any]) -> str:
    return " ".join(str(v) for v in (arguments or {}).values())


def _audit_event(actor: Actor, tool: str, decision: Decision, *, executed: bool,
                 args_text: str, latency_ms: int, redactions: list[str]) -> dict[str, Any]:
    return {
        "decision_id": decision.decision_id,
        "user_id": actor.user_id,
        "tenant": actor.tenant,
        "department": actor.department,
        "app_id": actor.app_id,
        "agent_id": actor.agent_id,
        "model_requested": f"tool:{tool}",
        "model_used": tool if executed else None,
        "data_classification": "",  # set by the caller (record) from the action
        "decision": decision.decision.value,
        "reason": decision.reason,
        "policy_version": decision.policy_version,
        "rules_matched": decision.rules_matched,
        "obligations_applied": decision.obligations,
        "redactions_applied": redactions,
        "log_level": decision.log_level.value,
        "estimated_tokens": 0,
        "total_tokens": 0,
        "latency_ms": latency_ms,
        "prompt_hash": hashlib.sha256(args_text.encode()).hexdigest(),
    }


@broker_router.post("/v1/tools/invoke")
async def invoke_tool(
    request: Request,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine: YamlPolicyEngine = request.app.state.engine
    tools: ToolRegistry = request.app.state.tools
    audit = request.app.state.audit

    started = time.perf_counter()
    tool = (body or {}).get("tool")
    arguments = (body or {}).get("arguments") or {}
    if not tool:
        raise HTTPException(status_code=400, detail="missing 'tool'")

    # Default-deny: unknown tools are never executed.
    if tools.get(tool) is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_tool", "tool": tool})

    # 1. Identity (verified delegation + live revocation), same as the model edge.
    try:
        actor = resolve_identity(authorization, settings, request.app.state.revocations)
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    # 2. Decision: same deterministic engine. Classification derived from args,
    #    never trusted from the caller.
    args_text = _args_text(arguments)
    classification = derive_classification([{"content": args_text}])
    action = CanonicalAIRequest(
        request_type="tool_call",
        model_requested=tool,
        tools_requested=[tool],
        data_classification=classification,
        actor=actor,
    )
    decision = engine.evaluate(action)

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    def record(executed: bool, redactions: list[str] | None = None) -> None:
        event = _audit_event(
            actor, tool, decision, executed=executed,
            args_text=args_text, latency_ms=elapsed(),
            redactions=redactions or [],
        )
        event["data_classification"] = action.data_classification.value
        audit.record(event)

    # 3. Enforce.
    if decision.decision == DecisionAction.DENY:
        record(executed=False)
        raise HTTPException(
            status_code=403,
            detail={
                "error": "denied_by_policy",
                "reason": decision.reason,
                "decision_id": decision.decision_id,
                "denied_tools": decision.denied_tools,
            },
        )
    if decision.decision == DecisionAction.APPROVAL_REQUIRED:
        record(executed=False)
        raise HTTPException(
            status_code=202,
            detail={
                "error": "approval_required",
                "reason": decision.reason,
                "decision_id": decision.decision_id,
            },
        )

    # 3b. Redaction obligation (same guarantee as the model edge): scrub PII/secrets
    # from arguments before they leave the plane, and from the tool's result before
    # it reaches the caller.
    redactions: list[str] = []
    exec_arguments = arguments
    if decision.redact:
        exec_arguments, arg_hits = scanner.redact_json(arguments, decision.redact)
        redactions.extend(h for h in arg_hits if h not in redactions)

    # 4. Execute with the broker's own credential (the agent never holds it).
    try:
        result = await tools.execute(tool, exec_arguments)
    except (ToolExecutionError, KeyError) as exc:
        record(executed=False, redactions=redactions)
        raise HTTPException(status_code=502, detail=f"tool_execution_error: {exc}") from exc

    if decision.redact:
        result, result_hits = scanner.redact_json(result, decision.redact)
        redactions.extend(h for h in result_hits if h not in redactions)

    record(executed=True, redactions=redactions)
    request.app.state.usage.record(
        {
            "tenant": actor.tenant,
            "user_id": actor.user_id,
            "edge": "tool",
            "resource": tool,
            "units": 1,
            "calls": 1,
            "decision_id": decision.decision_id,
        }
    )
    return {
        "result": result,
        "x_control_plane": {
            "decision_id": decision.decision_id,
            "policy_version": decision.policy_version,
            "rules_matched": decision.rules_matched,
            "obligations_applied": decision.obligations,
            "redactions_applied": redactions,
            "tool": tool,
        },
    }
