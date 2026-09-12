"""The authority service: one governed decision, fully explained.

Every enforcement surface (``POST /v1/authorize``, the MCP gateway, the demo
harness) calls :meth:`AuthorityService.decide`. It runs the whole chain and
returns a :class:`DecisionResult` whose ``trace`` is the product's core
artifact:

    identity -> task -> authority (with lineage) -> action -> resource
             -> consequence -> decision -> explanation

The chain is recorded on the signed audit event (``agent-plane.trace.v1``),
fed to the registry (so agents, tasks, and drift are discovered from
traffic), and served back by ``GET /v1/decisions/{id}``.

Outcomes: ALLOW, DENY, APPROVAL (``approval_required``), QUARANTINE (the
agent is held by an operator), SIMULATE (observe mode: computed, recorded,
not enforced; ``would_be`` says what enforce mode would have returned).
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent_plane.approvals.store import new_request
from agent_plane.authority.evaluator import (
    AuthorityDecision,
    AuthorityReason,
    evaluate_authority,
)
from agent_plane.authority.lease import AuthorityLease, action_matches, resource_matches
from agent_plane.authority.provenance import provenance_record
from agent_plane.consequence import IMPACT_RANK, Consequence
from agent_plane.consequence.catalog import REVERSIBILITY_RANK
from agent_plane.rules import compile_rules, compiled_lease_id
from agent_plane.schemas.canonical import Actor, DecisionAction

TRACE_SCHEMA = "agent-plane.trace.v1"

HTTP_STATUS = {
    DecisionAction.ALLOW: 200,
    DecisionAction.SIMULATE: 200,
    DecisionAction.APPROVAL_REQUIRED: 202,
    DecisionAction.DENY: 403,
    DecisionAction.QUARANTINE: 423,
}


@dataclass
class DecisionResult:
    outcome: DecisionAction
    reason: str
    decision_id: str
    lease_id: str | None
    approval_id: str | None
    context: dict[str, str]
    trace: dict[str, Any]
    would_be: str | None = None
    enforced: bool = True
    consequence: Consequence | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def http_status(self) -> int:
        return HTTP_STATUS[self.outcome]


# --------------------------------------------------------------------------- #
# Lineage
# --------------------------------------------------------------------------- #
def lineage(store: Any, lease_id: str | None, *, max_depth: int = 16) -> list[dict[str, Any]]:
    """Root-first chain of leases ending at ``lease_id``."""
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current = lease_id
    while current and current not in seen and len(chain) < max_depth:
        seen.add(current)
        lease = store.get(current)
        if lease is None:
            chain.append({"lease": current, "missing": True})
            break
        chain.append({
            "lease": lease.id, "subject": lease.subject, "tenant": lease.tenant, "task": lease.task,
            "actions": list(lease.actions), "resources": list(lease.resources),
            "protected_resources": list(lease.protected_resources),
            "require_approval": list(lease.require_approval),
            "permitted_consequence": dict(lease.permitted_consequence),
            "maximum_impact": lease.maximum_impact,
            "expires_at": lease.expires_at.isoformat() if lease.expires_at else None,
            "revoked": lease.revoked, "origin": dict(lease.origin), "parent_lease": lease.parent_lease,
        })
        current = lease.parent_lease
    chain.reverse()
    return chain


def lineage_permits(chain: list[dict[str, Any]], action: str) -> bool:
    return any(action in link.get("actions", []) for link in chain)


# --------------------------------------------------------------------------- #
# Consequence boundary
# --------------------------------------------------------------------------- #
def consequence_violations(lease: AuthorityLease, consequence: Consequence) -> list[str]:
    """Why ``consequence`` exceeds what ``lease`` permits (empty = within bounds)."""
    if not consequence.mutating:
        return []
    bounds = dict(lease.permitted_consequence)
    errors: list[str] = []
    # Ceiling from the lease's own maximum_impact: a reversible-only lease may not
    # cause an irreversible effect, whatever the caller declared.
    if lease.maximum_impact == "reversible" and consequence.reversibility == "irreversible":
        errors.append("irreversible effect under a reversible-only lease")
    max_impact = bounds.get("max_impact")
    if max_impact and IMPACT_RANK.get(consequence.impact, 4) > IMPACT_RANK.get(max_impact, 4):
        errors.append(f"impact {consequence.impact} exceeds permitted {max_impact}")
    envs = bounds.get("environments")
    if envs is not None:
        outside = [e for e in (consequence.environments or [consequence.environment]) if e not in envs]
        if outside:
            errors.append(f"mutates {', '.join(outside)} but only {', '.join(envs)} is permitted")
    if bounds.get("customer_facing") is False and consequence.customer_facing:
        errors.append("customer-facing workload is not permitted for this task")
    max_rev = bounds.get("max_reversibility")
    if max_rev and REVERSIBILITY_RANK.get(consequence.reversibility, 2) > REVERSIBILITY_RANK.get(max_rev, 2):
        errors.append(f"{consequence.reversibility} effect exceeds permitted {max_rev}")
    max_blast = bounds.get("max_blast_radius")
    if max_blast is not None and consequence.blast_radius > int(max_blast):
        errors.append(f"blast radius {consequence.blast_radius} exceeds permitted {max_blast}")
    return errors


# --------------------------------------------------------------------------- #
# Explanation
# --------------------------------------------------------------------------- #
def _scope(lease: AuthorityLease | None) -> str:
    return ", ".join(lease.resources) if lease and lease.resources else "no resources"


def explain(outcome: DecisionAction, reason: str, *, task: str, action: str, resource: str,
            lease: AuthorityLease | None, consequence: Consequence | None,
            chain: list[dict[str, Any]], violations: list[str], would_be: str | None) -> list[str]:
    lines: list[str] = []
    cons = ""
    if consequence and consequence.mutating:
        bits = [consequence.direct_effect]
        if consequence.customer_facing:
            bits.append("it is customer-facing")
        if consequence.downstream:
            bits.append(f"{len(consequence.downstream)} downstream resource(s) would be affected")
        cons = " " + "; ".join(bits) + "."
    path = " -> ".join(link.get("subject", link.get("lease", "?")) for link in chain) if chain else ""

    if reason == AuthorityReason.ACTION_WITHIN_TASK_AUTHORITY.value:
        lines.append(f"The task '{task}' grants {action} against {_scope(lease)}.")
        lines.append(f"{resource} is within that scope"
                     + (f" and the consequence ({consequence.impact} impact, {consequence.environment}) is within the permitted boundary." if consequence and consequence.mutating else "; the action does not change state."))
    elif reason == AuthorityReason.ACTION_APPROVED.value:
        lines.append(f"A human approved {action} against {resource} for task '{task}'. The approval was consumed; it cannot authorise a second execution.")
    elif reason == AuthorityReason.ACTION_REQUIRES_APPROVAL.value:
        lines.append(f"The task '{task}' permits {action} against {resource}, but the lease requires a human to approve this action before it runs.{cons}")
    elif reason == AuthorityReason.RESOURCE_OUTSIDE_DELEGATED_SCOPE.value:
        # The resource is out of scope, which says nothing about whether the
        # action was granted at all. Claiming "the task permits <action> only
        # against ..." for an action the task never granted would describe
        # authority that does not exist, so say which of the two it is.
        if lease is not None and action_matches(lease.actions, action):
            lines.append(f"The task '{task}' permits {action} only against {_scope(lease)}.")
        elif lease is not None:
            lines.append(f"The task '{task}' reaches {_scope(lease)} and never granted {action} at all.")
        else:
            lines.append(f"Nothing grants {action} for this task.")
        lines.append(f"The requested action targets {resource}.{cons}")
        lines.append("No authority lineage permits that consequence.")
    elif reason == AuthorityReason.RESOURCE_PROTECTED.value:
        lines.append(f"{resource} is explicitly protected by the task's authority lease; {action} against it is refused regardless of scope.{cons}")
    elif reason == AuthorityReason.ACTION_REFUSED_BY_RULE.value:
        lines.append(f"A rule for this project lists {action} as NEVER allowed.")
        lines.append("A never-rule is absolute: no other rule, lease, or delegation can grant it back.")
    elif reason == AuthorityReason.ACTION_NOT_AUTHORIZED.value:
        lines.append(f"No authority lineage permits {action}.")
        if path:
            lines.append(f"Lineage: {path}. The closest grant covers {', '.join(lease.actions) if lease else 'nothing'}.")
    elif reason == AuthorityReason.CONSEQUENCE_OUTSIDE_TASK_BOUNDARY.value:
        lines.append(f"The task's authority covers {action} on {resource}, but what it would cause exceeds the task boundary: {'; '.join(violations)}.")
        if cons:
            lines.append(cons.strip())
    elif reason == AuthorityReason.ACTION_IMPACT_EXCEEDS_LEASE.value:
        lines.append(f"The caller declared an impact above the lease's ceiling ({lease.maximum_impact if lease else 'unknown'}).")
    elif reason == AuthorityReason.NO_ACTIVE_LEASE.value:
        lines.append(f"No authority lease binds this agent to task '{task}'. Nothing was granted, so nothing is permitted.")
    elif reason == AuthorityReason.LEASE_REVOKED.value:
        lines.append("The task's authority was revoked by an operator; the underlying credential is unchanged but no longer authorises this task.")
    elif reason == AuthorityReason.LEASE_EXPIRED.value:
        lines.append("The task's authority expired.")
    elif reason == AuthorityReason.ACTION_LIMIT_EXCEEDED.value:
        lines.append(f"{action} has reached the use limit the lease set for it.")
    elif reason == AuthorityReason.ACTION_OUTSIDE_CAPABILITY_MANIFEST.value:
        lines.append(f"The agent's identity does not even declare the capability for {action}; task authority was not consulted.")
    elif reason == AuthorityReason.AGENT_QUARANTINED.value:
        lines.append("An operator quarantined this agent. Every action is held until the quarantine is lifted.")
    elif reason.startswith("APPROVAL_"):
        lines.append({
            "APPROVAL_PENDING": "The approval request is still waiting for a human decision.",
            "APPROVAL_REJECTED": "A human rejected this action.",
            "APPROVAL_EXPIRED": "The approval request expired before a decision was made.",
            "APPROVAL_ALREADY_USED": "This approval already authorised one execution; it cannot authorise another.",
            "APPROVAL_MISMATCH": "The approval was granted for a different action or resource.",
            "APPROVAL_NOT_FOUND": "No approval request matches this agent and id.",
        }.get(reason, reason))
    else:
        lines.append(reason)
    if outcome == DecisionAction.SIMULATE:
        lines.append(f"Observe mode: this would have been {str(would_be).upper()} under enforcement. Nothing was blocked; the attempt is recorded so this agent's authority can be described from what it actually does.")
    elif would_be:
        lines.append("Govern mode: the violation is recorded and flagged, but agent-plane is not blocking it. Switch this project to Enforce when you want the decision to bind.")
    return lines


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class AuthorityService:
    def __init__(self, state: Any):
        self.state = state  # app.state: settings, leases, approvals, registry, catalog, audit, usage, metrics, approval_notifier

    # -- helpers --------------------------------------------------------------- #
    def _mode(self, tenant: str) -> str:
        """observe | govern | enforce.

        A project carries its own mode, so a developer changes it in the UI and
        nothing else needs to know. Traffic that belongs to no project (a
        legacy JWT tenant) falls back to the runtime override, then to the
        deployment default.
        """
        accounts = getattr(self.state, "accounts", None)
        if accounts is not None:
            project = accounts.project(tenant)
            if project is not None:
                return project.mode
        registry = getattr(self.state, "agent_registry", None)
        default = getattr(self.state.settings, "enforcement_mode", "enforce")
        return registry.mode(tenant, default) if registry is not None else default

    def _apply_rules(self, actor: Actor, task: str, *, integration: str | None,
                     environment: str | None) -> None:
        """Compile the project's rules into an ephemeral lease for this task.

        Rules are the project's standing authority; explicitly issued leases are
        task grants. Both are evaluated together, which is what lets a rule's
        NEVER list refuse an action that some other grant would have allowed.
        """
        rules_store = getattr(self.state, "rules", None)
        if rules_store is None:
            return
        agent = actor.agent_id or actor.user_id
        rules = rules_store.list(actor.tenant, enabled_only=True)
        compiled = compile_rules(
            rules, project_id=actor.tenant, agent=agent, task=task,
            integration=integration, environment=environment,
        )
        if compiled is None:
            # No rule applies any more (disabled, deleted, or re-scoped). Revoke
            # whatever they previously compiled to, so turning a rule off takes
            # authority away instead of leaving a stale grant behind.
            stale_id = compiled_lease_id(actor.tenant, agent, task)
            previous = self.state.leases.get(stale_id)
            if previous is not None and not previous.revoked:
                self.state.leases.revoke(stale_id)
            return
        existing = self.state.leases.get(compiled.id)
        fingerprint = compiled.origin.get("fingerprint")
        stale = (existing is None or existing.revoked
                 or existing.origin.get("fingerprint") != fingerprint
                 or (existing.expires_at is not None and existing.expires_at <= datetime.now(UTC)))
        if stale:
            self.state.leases.add(compiled)

    def _resume(self, actor: Actor, approval_id: str, *, task: str, action: str, resource: str,
                impact: str) -> tuple[AuthorityDecision, str | None]:
        store = self.state.approvals
        decision_id = f"az_{uuid.uuid4().hex[:12]}"
        req = store.get(approval_id)
        subject = actor.agent_id or actor.user_id
        if req is None or req.subject != subject or req.tenant != actor.tenant:
            return AuthorityDecision(decision=DecisionAction.DENY, reason=AuthorityReason.APPROVAL_NOT_FOUND,
                                     decision_id=decision_id), None
        if (req.task, req.action, req.resource) != (task, action, resource):
            return AuthorityDecision(decision=DecisionAction.DENY, reason=AuthorityReason.APPROVAL_MISMATCH,
                                     lease_id=req.lease_id, decision_id=decision_id), req.id
        if req.status == "pending":
            return AuthorityDecision(decision=DecisionAction.APPROVAL_REQUIRED, reason=AuthorityReason.APPROVAL_PENDING,
                                     lease_id=req.lease_id, decision_id=decision_id), req.id
        if req.status != "approved":
            reason = {"rejected": AuthorityReason.APPROVAL_REJECTED, "expired": AuthorityReason.APPROVAL_EXPIRED,
                      "consumed": AuthorityReason.APPROVAL_ALREADY_USED}[req.status]
            return AuthorityDecision(decision=DecisionAction.DENY, reason=reason, lease_id=req.lease_id,
                                     decision_id=decision_id), req.id
        with self.state.leases.transaction():
            current = evaluate_authority(self.state.leases, actor, task=task, action=action, resource=resource,
                                         impact=impact, consume=False,
                                         lease_ids=frozenset([req.lease_id]) if req.lease_id else None)
            if current.decision == DecisionAction.DENY:
                return current, req.id
            if not store.consume(req.id):
                return AuthorityDecision(decision=DecisionAction.DENY, reason=AuthorityReason.APPROVAL_ALREADY_USED,
                                         lease_id=req.lease_id, decision_id=decision_id), req.id
        return AuthorityDecision(decision=DecisionAction.ALLOW, reason=AuthorityReason.ACTION_APPROVED,
                                 lease_id=req.lease_id, decision_id=decision_id), req.id

    # -- the decision ------------------------------------------------------------ #
    def decide(
        self, actor: Actor, *, task: str, action: str, resource: str, impact: str = "reversible",
        approval: str | None = None, context: dict[str, str] | None = None, edge: str = "authorize",
        consume: bool = True, lease_ids: frozenset[str] | None = None, record: bool = True,
        integration: str | None = None,
    ) -> DecisionResult:
        started = time.perf_counter()
        context = dict(context or {})
        settings = self.state.settings
        leases = self.state.leases
        registry = getattr(self.state, "agent_registry", None)
        catalog = getattr(self.state, "catalog", None)
        subject = actor.agent_id or actor.user_id
        mode = self._mode(actor.tenant)

        consequence: Consequence | None = catalog.evaluate(action, resource) if catalog is not None else None
        if lease_ids is None:
            self._apply_rules(actor, task, integration=integration,
                              environment=consequence.environment if consequence else None)
        violations: list[str] = []
        approval_ref: str | None = None
        would_be: str | None = None
        enforced = True

        # 1. Quarantine is absolute: it is an operator's hold on the agent.
        if registry is not None and registry.is_quarantined(actor.tenant, subject):
            decision = AuthorityDecision(decision=DecisionAction.QUARANTINE, reason=AuthorityReason.AGENT_QUARANTINED,
                                         decision_id=f"az_{uuid.uuid4().hex[:12]}")
        # 2. Resume a granted approval.
        elif approval:
            decision, approval_ref = self._resume(actor, approval, task=task, action=action, resource=resource, impact=impact)
        # 3. Fresh evaluation: authority, then consequence, then use reservation.
        else:
            with leases.transaction():
                decision = evaluate_authority(leases, actor, task=task, action=action, resource=resource,
                                              impact=impact, consume=False, lease_ids=lease_ids)
                lease = leases.get(decision.lease_id) if decision.lease_id else None
                if decision.decision in (DecisionAction.ALLOW, DecisionAction.APPROVAL_REQUIRED) and lease is not None:
                    if consequence is not None:
                        violations = consequence_violations(lease, consequence)
                    if violations:
                        decision = AuthorityDecision(decision=DecisionAction.DENY,
                                                     reason=AuthorityReason.CONSEQUENCE_OUTSIDE_TASK_BOUNDARY,
                                                     lease_id=lease.id, decision_id=decision.decision_id)
                    elif consume and mode == "enforce":
                        # Legacy behaviour: an approval-required check also spends a use.
                        if not leases.try_consume(lease.id, action, lease.max_uses.get(action)):
                            decision = AuthorityDecision(decision=DecisionAction.DENY,
                                                         reason=AuthorityReason.ACTION_LIMIT_EXCEEDED,
                                                         lease_id=lease.id, decision_id=decision.decision_id)
                    elif consume and mode == "observe" and decision.decision == DecisionAction.ALLOW:
                        leases.try_consume(lease.id, action, lease.max_uses.get(action))
            if decision.decision == DecisionAction.APPROVAL_REQUIRED and mode == "enforce":
                lease = leases.get(decision.lease_id) if decision.lease_id else None
                req = new_request(tenant=actor.tenant, subject=subject, task=task, action=action, resource=resource,
                                  lease_id=decision.lease_id, evidence_id=decision.decision_id,
                                  ttl_seconds=settings.approval_ttl_seconds, context=context,
                                  lease_expires_at=lease.expires_at if lease else None)
                self.state.approvals.create(req)
                approval_ref = req.id

        # 4. Only enforce mode binds. Observe reports SIMULATE and hides nothing;
        #    govern reports the real decision but leaves execution to the caller.
        if mode != "enforce" and decision.decision in (DecisionAction.DENY, DecisionAction.APPROVAL_REQUIRED):
            would_be = decision.decision.value
            enforced = False
            if mode == "observe":
                decision = AuthorityDecision(decision=DecisionAction.SIMULATE, reason=decision.reason,
                                             lease_id=decision.lease_id, decision_id=decision.decision_id)

        lease = leases.get(decision.lease_id) if decision.lease_id else None
        if lease is None:
            # No lease matched the resource/action: the agent may still hold
            # authority for this task. Show it, so the trace can say "the task
            # permits X only against staging/*" instead of "no authority".
            held = [ls for ls in leases.for_subject_task(subject, task, actor.tenant)
                    if lease_ids is None or ls.id in lease_ids]
            held = [ls for ls in held if not ls.revoked] or held
            lease = held[0] if held else None
        chain = lineage(leases, lease.id) if lease else []
        task_record = registry.task(actor.tenant, task) if registry is not None else None
        explanation = explain(decision.decision, decision.reason.value, task=task, action=action, resource=resource,
                              lease=lease, consequence=consequence, chain=chain, violations=violations,
                              would_be=would_be)
        scope_state = ("protected" if lease and resource_matches(lease.protected_resources, resource)
                       else "within" if lease and resource_matches(lease.resources, resource)
                       else "outside")
        trace: dict[str, Any] = {
            "schema": TRACE_SCHEMA,
            "decision_id": decision.decision_id,
            "edge": edge,
            "mode": mode,
            "identity": {"agent": subject, "user": actor.user_id, "tenant": actor.tenant,
                         "application": actor.app_id, "verified": settings.identity_mode == "delegation",
                         "identity_mode": settings.identity_mode, "declared_capabilities": list(actor.allowed_tools)},
            "task": {"id": task, "origin": task_record.origin.model_dump(mode="json") if task_record else {},
                     "agents": task_record.agents if task_record else [subject]},
            "authority": {"lease": lease.id if lease else None, "actions": lease.actions if lease else [],
                          "resources": lease.resources if lease else [],
                          "protected_resources": lease.protected_resources if lease else [],
                          "require_approval": lease.require_approval if lease else [],
                          "permitted_consequence": lease.permitted_consequence if lease else {},
                          "maximum_impact": lease.maximum_impact if lease else None,
                          "expires_at": lease.expires_at.isoformat() if lease and lease.expires_at else None,
                          "origin": lease.origin if lease else {}, "lineage": chain,
                          "lineage_permits_action": lineage_permits(chain, action)},
            "action": {"name": action, "declared_impact": impact,
                       "effect": consequence.effect if consequence else None},
            "resource": {"name": resource, "scope": scope_state},
            "consequence": consequence.model_dump(mode="json") if consequence else None,
            "decision": {"outcome": decision.decision.value, "reason": decision.reason.value,
                         "reasons": [decision.reason.value] + (["CONSEQUENCE:" + v for v in violations]),
                         "enforced": enforced, "would_be": would_be, "approval_id": approval_ref},
            "explanation": explanation,
            "context": context,
        }

        if record:
            self._record(actor, trace, decision, task=task, action=action, resource=resource, context=context,
                         approval_ref=approval_ref, started=started, edge=edge)

        payload: dict[str, Any] = {
            "decision": decision.decision.value,
            "reason": decision.reason.value,
            "lease": decision.lease_id,
            "evidence_id": decision.decision_id,
            "enforced": enforced,
        }
        payload["mode"] = mode
        if would_be:
            payload["would_be"] = would_be
            payload["advisory"] = True
        if approval_ref:
            payload["approval_id"] = approval_ref
        if context:
            payload["context"] = context
        if consequence is not None:
            payload["consequence"] = {"impact": consequence.impact, "environment": consequence.environment,
                                      "reversibility": consequence.reversibility,
                                      "customer_facing": consequence.customer_facing,
                                      "blast_radius": consequence.blast_radius, "summary": consequence.summary}
        payload["explanation"] = explanation
        return DecisionResult(outcome=decision.decision, reason=decision.reason.value, decision_id=decision.decision_id,
                              lease_id=decision.lease_id, approval_id=approval_ref, context=context, trace=trace,
                              would_be=would_be, enforced=enforced, consequence=consequence, payload=payload)

    # -- side effects ------------------------------------------------------------- #
    def _record(self, actor: Actor, trace: dict[str, Any], decision: AuthorityDecision, *, task: str,
                action: str, resource: str, context: dict[str, str], approval_ref: str | None,
                started: float, edge: str) -> None:
        obligations: list[Any] = [trace]
        if context:
            obligations.append(provenance_record(context))
        if approval_ref:
            obligations.append({"schema": "agent-plane.approval.v1", "approval_id": approval_ref})
        self.state.audit.record({
            "decision_id": decision.decision_id, "user_id": actor.user_id, "tenant": actor.tenant,
            "department": actor.department, "app_id": actor.app_id, "agent_id": actor.agent_id,
            "model_requested": f"authorize:{action}", "model_used": resource, "data_classification": "",
            "decision": decision.decision.value, "reason": decision.reason.value,
            "rules_matched": [decision.lease_id] if decision.lease_id else [],
            "obligations_applied": obligations, "latency_ms": int((time.perf_counter() - started) * 1000),
            "prompt_hash": hashlib.sha256(f"{task}:{action}:{resource}".encode()).hexdigest(),
        })
        usage = getattr(self.state, "usage", None)
        if usage is not None:
            usage.record({"tenant": actor.tenant, "user_id": actor.user_id, "edge": edge, "resource": action,
                          "units": 1, "calls": 1, "decision_id": decision.decision_id})
        metrics = getattr(self.state, "metrics", None)
        if metrics is not None:
            metrics.observe_decision(edge, decision.decision.value, decision.reason.value)
        registry = getattr(self.state, "agent_registry", None)
        if registry is not None:
            registry.observe(tenant=actor.tenant, agent=actor.agent_id or actor.user_id, application=actor.app_id,
                             declared=list(actor.allowed_tools), task=task, action=action, resource=resource,
                             outcome=decision.decision.value, decision_id=decision.decision_id, context=context, edge=edge)
        notifier = getattr(self.state, "approval_notifier", None)
        if approval_ref and notifier is not None and decision.decision == DecisionAction.APPROVAL_REQUIRED:
            req = self.state.approvals.get(approval_ref)
            if req is not None:
                notifier.emit("approval.requested", req.model_dump(mode="json"))
