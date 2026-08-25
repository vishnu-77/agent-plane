"""The agent->tool broker edge with optional AI-TM task-authority enforcement."""
from __future__ import annotations

import hashlib
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from agent_plane.authority.schema import AuthorityContext
from agent_plane.config import Settings
from agent_plane.gateway.identity import IdentityError, resolve_identity
from agent_plane.guardrails.classifier import derive_classification
from agent_plane.policy.engine import YamlPolicyEngine
from agent_plane.routing.tools import ToolExecutionError, ToolRegistry
from agent_plane.schemas.canonical import Actor, CanonicalAIRequest, Decision, DecisionAction

broker_router = APIRouter()


def _args_text(arguments: dict[str, Any]) -> str:
    return " ".join(str(v) for v in (arguments or {}).values())


def _audit_event(actor: Actor, tool: str, decision: Decision, *, executed: bool,
                 args_text: str, latency_ms: int) -> dict[str, Any]:
    return {
        "decision_id": decision.decision_id,
        "user_id": actor.user_id,
        "tenant": actor.tenant,
        "department": actor.department,
        "app_id": actor.app_id,
        "agent_id": actor.agent_id,
        "model_requested": f"tool:{tool}",
        "model_used": tool if executed else None,
        "data_classification": "",
        "decision": decision.decision.value,
        "reason": decision.reason,
        "policy_version": decision.policy_version,
        "rules_matched": decision.rules_matched,
        "obligations_applied": decision.obligations,
        "redactions_applied": [],
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
    authority = request.app.state.authority
    tools: ToolRegistry = request.app.state.tools
    audit = request.app.state.audit

    started = time.perf_counter()
    tool = (body or {}).get("tool")
    arguments = (body or {}).get("arguments") or {}
    authority_context = AuthorityContext.model_validate((body or {}).get("authority") or {})
    if not tool:
        raise HTTPException(status_code=400, detail="missing 'tool'")

    if tools.get(tool) is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_tool", "tool": tool})

    try:
        actor = resolve_identity(authorization, settings, request.app.state.revocations)
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

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

    # AI-TM runtime authority is an additional narrowing step. A valid identity,
    # tool grant and policy allow do not imply that this concrete action is
    # authorised for the current task/resource/environment.
    authority_decision = None
    if authority is not None and decision.decision == DecisionAction.ALLOW:
        authority_decision = authority.evaluate(
            agent_id=actor.agent_id,
            tool=tool,
            context=authority_context,
        )
        decision.rules_matched.append("ai-tm-task-authority")
        decision.obligations.append(
            f"authority_manifest:{authority_decision.manifest_version}"
        )
        decision.reason = authority_decision.reason
        if authority_decision.decision == "deny":
            decision.decision = DecisionAction.DENY
        elif authority_decision.decision == "approval_required":
            decision.decision = DecisionAction.APPROVAL_REQUIRED
            decision.requires_human_approval = True

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    def record(executed: bool) -> None:
        event = _audit_event(
            actor, tool, decision, executed=executed,
            args_text=args_text, latency_ms=elapsed(),
        )
        event["data_classification"] = action.data_classification.value
        if authority_decision is not None:
            event["authority_manifest_version"] = authority_decision.manifest_version
            event["authority_rule_index"] = authority_decision.rule_index
            event["task"] = authority_context.task
            event["environment"] = authority_context.environment
            event["resource"] = authority_context.resource
        audit.record(event)

    if decision.decision == DecisionAction.DENY:
        record(executed=False)
        raise HTTPException(
            status_code=403,
            detail={
                "error": "denied_by_policy",
                "reason": decision.reason,
                "decision_id": decision.decision_id,
                "denied_tools": decision.denied_tools,
                "authority_manifest_version": (
                    authority_decision.manifest_version if authority_decision else None
                ),
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
                "authority_manifest_version": (
                    authority_decision.manifest_version if authority_decision else None
                ),
            },
        )

    try:
        result = await tools.execute(tool, arguments)
    except (ToolExecutionError, KeyError) as exc:
        record(executed=False)
        raise HTTPException(status_code=502, detail=f"tool_execution_error: {exc}") from exc

    record(executed=True)
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
            "tool": tool,
            "authority_manifest_version": (
                authority_decision.manifest_version if authority_decision else None
            ),
        },
    }
