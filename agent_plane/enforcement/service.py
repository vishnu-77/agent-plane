"""Admission and dispatch for configured MCP tools. No authority is inferred
from prompts. Authority state and the request-key ledger live in the lease
store, so with the SQL store every replica sees the same leases, use counts,
and in-flight request keys."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime

from agent_plane.approvals.store import new_request
from agent_plane.authority.evaluator import evaluate_authority
from agent_plane.authority.service import consequence_violations
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
                        and lease.tenant == actor.tenant
                        and not lease.revoked and (lease.expires_at is None or lease.expires_at > datetime.now(UTC))
                        and tool.action in lease.actions and name in actor.allowed_tools
                        and any(c in actor.allowed_tools for c in ("*", tool.action, tool.action.split('.')[0]))
                        and (lease.max_uses.get(tool.action) is None or self.app.state.leases.use_count(lease.id, tool.action) < lease.max_uses[tool.action]))

    def _approvals(self):
        return getattr(self.app.state, "approvals", None)

    def _open_approval(self, actor: Actor, context: dict, lease) -> str | None:
        """Raise an approval request for an APPROVAL_REQUIRED admission."""
        store = self._approvals()
        if store is None:
            return None
        settings = getattr(self.app.state, "settings", None)
        req = new_request(
            tenant=actor.tenant, subject=actor.agent_id or actor.user_id, task=context["task"],
            action=context["action"], resource=context["resource"], lease_id=context["lease_id"],
            evidence_id=context["admission_id"],
            ttl_seconds=getattr(settings, "approval_ttl_seconds", 3600),
            context={"tool": context["tool"], "protocol_surface": "mcp",
                     "argument_digest": context["argument_digest"]},
            lease_expires_at=lease.expires_at if lease else None,
        )
        store.create(req)
        notifier = getattr(self.app.state, "approval_notifier", None)
        if notifier is not None:
            notifier.emit("approval.requested", req.model_dump(mode="json"))
        return req.id

    def _resume_approval(self, actor: Actor, approval_id: str, context: dict) -> tuple[str, str]:
        """Validate an approval for this exact admission. Returns (decision, reason);
        an approved request is consumed here, atomically, before any use is spent."""
        store = self._approvals()
        req = store.get(approval_id) if store is not None else None
        subject = actor.agent_id or actor.user_id
        if req is None or req.subject != subject or req.tenant != actor.tenant:
            return "deny", "APPROVAL_NOT_FOUND"
        if (req.task, req.action, req.resource, req.context.get("argument_digest")) != (
            context["task"], context["action"], context["resource"], context["argument_digest"]
        ):
            return "deny", "APPROVAL_MISMATCH"
        if req.status == "pending":
            return "approval_required", "APPROVAL_PENDING"
        if req.status != "approved":
            return "deny", {"rejected": "APPROVAL_REJECTED", "expired": "APPROVAL_EXPIRED",
                            "consumed": "APPROVAL_ALREADY_USED"}[req.status]
        if not store.consume(req.id):
            return "deny", "APPROVAL_ALREADY_USED"
        return "allow", "ACTION_APPROVED"

    async def invoke(self, actor: Actor, name: str, arguments: dict, request_key: str | None = None,
                     approval_id: str | None = None):
        if name not in self.tools:
            raise ValueError("Unknown or unmapped tool")
        if approval_id is not None and (not isinstance(approval_id, str) or not 1 <= len(approval_id) <= 64):
            raise ValueError("Invalid approval id")
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
        ledger = self.app.state.leases  # shared across replicas when the store is durable
        if key:
            if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128:
                raise ValueError("Invalid request key")
            seen = ledger.request_lookup(key)
            if seen is not None:
                previous, result = seen
                if previous != digest or result is None:
                    raise ValueError("Request key conflict or request already in progress; not retried")
                return result
        if self.active >= self.config.max_concurrency:
            raise ValueError("Gateway concurrency limit reached")
        if key:
            ledger.request_reserve(key, digest, self.config.max_request_keys)
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
                catalog = getattr(self.app.state, "catalog", None)
                if catalog is not None and lease is not None and decision in ("allow", "approval_required"):
                    consequence = catalog.evaluate(tool.action, resource)
                    violations = consequence_violations(lease, consequence)
                    context["consequence"] = consequence.model_dump(mode="json")
                    if violations:
                        decision, reason = "deny", "CONSEQUENCE_OUTSIDE_TASK_BOUNDARY"
                        context["consequence_violations"] = violations
                registry = getattr(self.app.state, "agent_registry", None)
                if registry is not None and registry.is_quarantined(actor.tenant, actor.agent_id or actor.user_id):
                    decision, reason = "quarantine", "AGENT_QUARANTINED"
                if approval_id and decision != "deny":
                    # Resume: a granted approval stands in for the approval gate, never for a dead lease.
                    decision, reason = self._resume_approval(actor, approval_id, context)
                    context["approval_id"] = approval_id
                elif decision == "approval_required":
                    context["approval_id"] = self._open_approval(actor, context, lease)
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
                ledger.request_complete(key, digest, result)
            registry = getattr(self.app.state, "agent_registry", None)
            if registry is not None:
                registry.observe(tenant=actor.tenant, agent=actor.agent_id or actor.user_id, application=actor.app_id,
                                 declared=list(actor.allowed_tools), task=binding.task, action=tool.action,
                                 resource=resource, outcome=decision, decision_id=context["admission_id"],
                                 context={"framework": "mcp"}, edge="mcp",
                                 executed=result.get("result") is not None if decision == "allow" else None)
            return result
        finally:
            self.active -= 1
