"""Single-process admission and dispatch. No authority is inferred from prompts."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime

from agent_plane.authority.evaluator import evaluate_authority
from agent_plane.enforcement.mapping import GatewayConfig
from agent_plane.guardrails import scanner
from agent_plane.guardrails.classifier import derive_classification
from agent_plane.schemas.canonical import Actor, CanonicalAIRequest, DecisionAction


def evidence_context(raw: dict) -> dict | None:
    return next((item for item in raw.get("obligations_applied", [])
                 if isinstance(item, dict) and item.get("schema") == "agent-plane.gateway.v1"), None)


class EnforcementService:
    def __init__(self, app, config: GatewayConfig, upstream):
        self.app = app
        self.config = config
        self.upstream = upstream
        self.tools = {tool.name: tool for tool in config.tools}
        self.active = 0
        self.requests: dict[tuple, tuple[str, dict | None]] = {}

    def binding(self, actor: Actor):
        if not actor.agent_id or not actor.allowed_tools:
            raise ValueError("Explicit agent identity and capabilities are required")
        binding = next((b for b in self.config.bindings
                        if (b.tenant, b.agent) == (actor.tenant, actor.agent_id)), None)
        if binding is None:
            raise ValueError("No trusted task binding for this agent")
        return binding

    def record(self, actor, context, *, phase, outcome, decision, reason, policy=None):
        data = {**context, "schema": "agent-plane.gateway.v1", "phase": phase,
                "execution_status": outcome, "decision": decision, "reason": reason,
                "recorded_at": datetime.now(UTC).isoformat()}
        self.app.state.audit.record({
            "decision_id": context["admission_id"] if phase == "decision" else f"gx_{uuid.uuid4().hex[:16]}",
            "user_id": actor.user_id, "tenant": actor.tenant, "agent_id": actor.agent_id,
            "app_id": actor.app_id, "department": actor.department,
            "model_requested": f"authorize:{context['action']}" if phase == "decision" else "gateway-receipt:" + context["action"],
            "model_used": context["resource"], "data_classification": "",
            "decision": decision, "reason": reason,
            "policy_version": policy.policy_version if policy else None,
            "rules_matched": [context["lease_id"]] if context.get("lease_id") else [],
            "obligations_applied": [data], "prompt_hash": context["argument_digest"],
        })
        return data

    def eligible(self, actor: Actor, name: str) -> bool:
        """Discovery is a candidate filter, never a consuming authorization."""
        try:
            binding = self.binding(actor)
        except ValueError:
            return False
        tool = self.tools[name]
        policy = self.app.state.engine.evaluate(CanonicalAIRequest(
            request_type="tool_call", model_requested=name, tools_requested=[name], actor=actor))
        if policy.decision == DecisionAction.DENY:
            return False
        with self.app.state.leases.transaction():
            lease = self.app.state.leases.get(binding.lease)
            return bool(lease and lease.subject == actor.agent_id and lease.task == binding.task
                        and not lease.revoked and (lease.expires_at is None or lease.expires_at > datetime.now(UTC))
                        and tool.action in lease.actions and name in actor.allowed_tools
                        and any(c in actor.allowed_tools for c in ("*", tool.action, tool.action.split('.')[0]))
                        and (lease.max_uses.get(tool.action) is None or self.app.state.leases.use_count(lease.id, tool.action) < lease.max_uses[tool.action]))

    async def invoke(self, actor: Actor, name: str, arguments: dict, request_key: str | None = None):
        if name not in self.tools:
            raise ValueError("Unknown or unmapped tool")
        binding = self.binding(actor)
        tool = self.tools[name]
        # Validate before policy, then revalidate and resolve the exact final arguments.
        tool.resolve(arguments)
        policy = self.app.state.engine.evaluate(CanonicalAIRequest(
            request_type="tool_call", model_requested=name, tools_requested=[name], actor=actor,
            data_classification=derive_classification([{"content": json.dumps(arguments)}]),
        ))
        forwarded, _ = scanner.redact_json(arguments, policy.redact) if policy.redact else (arguments, [])
        resource = tool.resolve(forwarded)
        digest = hashlib.sha256(json.dumps([name, forwarded], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        key = (actor.tenant, actor.agent_id, request_key) if request_key else None
        if key:
            if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128:
                raise ValueError("Invalid request key")
            if key in self.requests:
                previous, result = self.requests[key]
                if previous != digest or result is None:
                    raise ValueError("Request key conflict or request already in progress; not retried")
                return result
            if len(self.requests) >= 10000:
                raise ValueError("Request key capacity reached; restart only after reconciling outcomes")
        if self.active >= self.config.max_concurrency:
            raise ValueError("Gateway concurrency limit reached")
        if key:
            self.requests[key] = (digest, None)
        self.active += 1
        context = {"admission_id": f"az_{uuid.uuid4().hex[:16]}", "task": binding.task,
                   "tool": name, "upstream_tool": tool.upstream_tool, "action": tool.action,
                   "resource": resource, "argument_digest": digest, "mode": "enforce",
                   "lease_id": binding.lease, "protocol_surface": "mcp"}
        try:
            with self.app.state.leases.transaction():
                authority = evaluate_authority(self.app.state.leases, actor, task=binding.task,
                    action=tool.action, resource=resource, consume=False, lease_ids=frozenset([binding.lease]))
                decision, reason = authority.decision.value, authority.reason.value
                if policy.decision == DecisionAction.DENY or (decision == "allow" and policy.decision == DecisionAction.APPROVAL_REQUIRED):
                    decision, reason = policy.decision.value, policy.reason or "TOOL_POLICY_REQUIRES_APPROVAL"
                lease = self.app.state.leases.get(binding.lease)
                context["lease_snapshot"] = lease.model_dump(mode="json") if lease else None
                data = self.record(actor, context, phase="decision", outcome="not_dispatched", decision=decision, reason=reason, policy=policy)
                if decision == "allow":
                    # The lock covers policy-independent authority checks, required audit
                    # persistence, and use reservation. Only admitted ALLOW spends a use.
                    if lease is None or not self.app.state.leases.try_consume(
                        lease.id, tool.action, lease.max_uses.get(tool.action)
                    ):
                        raise RuntimeError("Authority use reservation failed")
            if decision != "allow":
                result = {"decision": decision, "reason": reason, "evidence": data, "result": None}
            else:
                self.record(actor, context, phase="execution", outcome="dispatch_started", decision=decision, reason="UPSTREAM_DISPATCH_STARTED")
                try:
                    async with asyncio.timeout(self.config.timeout_seconds):
                        upstream_result = await self.upstream(tool, forwarded)
                    result_dict = upstream_result.model_dump(mode="json", by_alias=True, exclude_none=True)
                    if len(json.dumps(result_dict).encode()) > self.config.max_response_bytes:
                        raise ValueError("Upstream result exceeds configured bound")
                    if policy.redact:
                        result_dict, _ = scanner.redact_json(result_dict, policy.redact)
                    outcome = "upstream_error" if upstream_result.is_error else "completed"
                    data = self.record(actor, context, phase="execution", outcome=outcome, decision=decision, reason="UPSTREAM_RESULT_RECEIVED")
                    result = {"decision": decision, "reason": reason, "evidence": data, "result": result_dict}
                except (Exception, asyncio.CancelledError) as exc:
                    # Once dispatch starts, generic transport failure cannot prove no effect.
                    data = self.record(actor, context, phase="execution", outcome="outcome_unknown", decision=decision, reason="UPSTREAM_OUTCOME_UNKNOWN")
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    result = {"decision": decision, "reason": "UPSTREAM_OUTCOME_UNKNOWN", "evidence": data, "result": None}
            if key:
                self.requests[key] = (digest, result)
            return result
        finally:
            self.active -= 1
