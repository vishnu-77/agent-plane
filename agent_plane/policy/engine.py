"""The Policy Decision Point (PDP).

Evaluates a :class:`CanonicalAIRequest` against the loaded :class:`PolicyBundle`
and returns a :class:`Decision`. This is the in-process YAML engine; the
``PolicyDecisionPoint`` Protocol lets an OPA/Cedar-backed PDP drop in later
without touching the gateway.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Protocol

from agent_plane.policy.loader import PolicyBundle
from agent_plane.policy.schema import Policy
from agent_plane.schemas.canonical import (
    CanonicalAIRequest,
    Decision,
    DecisionAction,
    LogLevel,
)

# Resolves a model id -> provider tags, e.g. "gpt-4.1" -> {"openai", "external"}.
ProviderResolver = Callable[[str], set[str]]

_SEVERITY = {
    DecisionAction.ALLOW: 0,
    DecisionAction.APPROVAL_REQUIRED: 1,
    DecisionAction.DENY: 2,
}

_ACTION_MAP = {
    "allow": DecisionAction.ALLOW,
    "allow_with_filters": DecisionAction.ALLOW,
    "approval_required": DecisionAction.APPROVAL_REQUIRED,
    "deny": DecisionAction.DENY,
}


class PolicyDecisionPoint(Protocol):
    def evaluate(self, request: CanonicalAIRequest) -> Decision: ...


class YamlPolicyEngine:
    def __init__(
        self,
        bundle: PolicyBundle,
        provider_resolver: ProviderResolver | None = None,
    ):
        self.bundle = bundle
        self._provider_tags = provider_resolver or (lambda _m: set())

    # --- matching helpers -------------------------------------------------
    def _in_scope(self, policy: Policy, req: CanonicalAIRequest) -> bool:
        scope = policy.scope
        if scope.tenants and req.actor.tenant not in scope.tenants:
            return False
        if scope.departments and req.actor.department not in scope.departments:
            return False
        return True

    def _matches(self, policy: Policy, req: CanonicalAIRequest) -> bool:
        m = policy.match
        if m.request_type and m.request_type != req.request_type:
            return False
        if m.data_classification and (
            req.data_classification.value not in m.data_classification
        ):
            return False
        if m.model_requested and req.model_requested not in m.model_requested:
            return False
        if m.model_provider:
            tags = self._provider_tags(req.model_requested)
            if not (set(m.model_provider) & tags):
                return False
        if m.actor_type:
            is_agent = req.actor.agent_id is not None
            actual = "agent" if is_agent else "user"
            if m.actor_type != actual:
                return False
        if m.departments and req.actor.department not in m.departments:
            return False
        if m.tools and not (set(m.tools) & set(req.tools_requested)):
            return False
        return True

    def _exception_for(self, policy: Policy, req: CanonicalAIRequest):
        tags = self._provider_tags(req.model_requested)
        for exc in policy.exceptions:
            if exc.model_provider and exc.model_provider not in tags:
                continue
            if exc.model_requested and exc.model_requested != req.model_requested:
                continue
            return exc
        return None

    # --- evaluation -------------------------------------------------------
    def evaluate(self, request: CanonicalAIRequest) -> Decision:
        decision = Decision(
            decision=DecisionAction.ALLOW,
            route=request.model_requested,
            policy_version=self.bundle.version,
            decision_id=f"dec_{uuid.uuid4().hex[:12]}",
        )

        for policy in self.bundle.policies:
            if not self._in_scope(policy, request):
                continue
            if not self._matches(policy, request):
                continue

            spec = policy.decision
            action = _ACTION_MAP.get(spec.action, DecisionAction.ALLOW)
            obligations = list(policy.obligations) + list(spec.obligations)

            # A deny may be softened by a matching exception.
            if action == DecisionAction.DENY:
                exc = self._exception_for(policy, request)
                if exc is not None:
                    action = _ACTION_MAP.get(exc.action, DecisionAction.ALLOW)
                    obligations += list(exc.obligations)

            decision.rules_matched.append(policy.name)
            decision.obligations.extend(
                o for o in obligations if o not in decision.obligations
            )
            for field in spec.redact:
                if field not in decision.redact:
                    decision.redact.append(field)
            if spec.route:
                decision.route = spec.route
            if spec.max_tokens is not None:
                decision.max_tokens = spec.max_tokens
            if spec.cache_ttl is not None:
                decision.cache_ttl = spec.cache_ttl
            if spec.log_level:
                decision.log_level = LogLevel(spec.log_level)

            # Most restrictive action wins across all matched policies.
            if _SEVERITY[action] >= _SEVERITY[decision.decision]:
                decision.decision = action
                if spec.reason:
                    decision.reason = spec.reason

        # Least-privilege tool enforcement (identity-scoped). When the actor
        # carries an explicit ``allowed_tools`` grant, any requested tool outside
        # it is a hard deny — this can only tighten the decision above. An empty
        # grant means "not scoped at the identity layer"; restrict such cases via
        # policy (e.g. a match.tools rule) instead.
        if request.tools_requested and request.actor.allowed_tools:
            allowed = set(request.actor.allowed_tools)
            denied = [t for t in request.tools_requested if t not in allowed]
            if denied:
                decision.denied_tools = denied
                decision.decision = DecisionAction.DENY
                decision.reason = (
                    f"Tools not permitted for this actor: {', '.join(denied)}"
                )

        decision.requires_human_approval = (
            decision.decision == DecisionAction.APPROVAL_REQUIRED
        )
        # "redact_pii" obligation implies the default PII field set.
        if "redact_pii" in decision.obligations and not decision.redact:
            decision.redact = ["email", "credit_card", "phone", "api_key"]
        return decision
