"""The Authority Evaluator: decides ALLOW / DENY / APPROVAL_REQUIRED for one
proposed (task, action, resource), pre-execution.

Two independent gates, in order:

1. **Capability** - does the actor's capability manifest (``Actor.allowed_tools``)
   cover this action's namespace at all? This is the identity layer's static
   grant, same one the tool broker enforces.
2. **Task authority** - do any of the actor's active :class:`AuthorityLease`
   grants for this *task* (in this *tenant*) cover this *resource* and
   *action*, under their constraints (protected resources, use limits, expiry,
   and the proposed action's declared ``impact`` against the lease's
   ``maximum_impact`` ceiling)?

Capability without task authority is exactly the "agent holds
github.delete_repository but this task only authorises branch cleanup on one
repo" case - denied at gate 2, not gate 1.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel

from agent_plane.authority.lease import IMPACT_RANK, resource_matches
from agent_plane.authority.store import LeaseStore
from agent_plane.schemas.canonical import Actor, DecisionAction


class AuthorityReason(str, Enum):
    ACTION_OUTSIDE_CAPABILITY_MANIFEST = "ACTION_OUTSIDE_CAPABILITY_MANIFEST"
    NO_ACTIVE_LEASE = "NO_ACTIVE_LEASE"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    LEASE_REVOKED = "LEASE_REVOKED"
    RESOURCE_OUTSIDE_DELEGATED_SCOPE = "RESOURCE_OUTSIDE_DELEGATED_SCOPE"
    RESOURCE_PROTECTED = "RESOURCE_PROTECTED"
    ACTION_NOT_AUTHORIZED = "ACTION_NOT_AUTHORIZED"
    ACTION_IMPACT_EXCEEDS_LEASE = "ACTION_IMPACT_EXCEEDS_LEASE"
    ACTION_LIMIT_EXCEEDED = "ACTION_LIMIT_EXCEEDED"
    ACTION_WITHIN_TASK_AUTHORITY = "ACTION_WITHIN_TASK_AUTHORITY"
    ACTION_REQUIRES_APPROVAL = "ACTION_REQUIRES_APPROVAL"
    # Consequence boundary (agent_plane.consequence): the action is in scope but
    # what it would cause exceeds what the task's lease permits.
    CONSEQUENCE_OUTSIDE_TASK_BOUNDARY = "CONSEQUENCE_OUTSIDE_TASK_BOUNDARY"
    # An operator quarantined the agent; nothing proceeds until lifted.
    AGENT_QUARANTINED = "AGENT_QUARANTINED"
    # Approval resume path (POST /v1/authorize with "approval": "<id>")
    ACTION_APPROVED = "ACTION_APPROVED"
    APPROVAL_PENDING = "APPROVAL_PENDING"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_ALREADY_USED = "APPROVAL_ALREADY_USED"
    APPROVAL_MISMATCH = "APPROVAL_MISMATCH"
    APPROVAL_NOT_FOUND = "APPROVAL_NOT_FOUND"


class AuthorityDecision(BaseModel):
    decision: DecisionAction
    reason: AuthorityReason
    lease_id: str | None = None
    decision_id: str

    @property
    def allowed(self) -> bool:
        return self.decision == DecisionAction.ALLOW


def _capability_covers(actor: Actor, action: str) -> bool:
    """Empty grant = not scoped at the identity layer (matches the policy
    engine's ``allowed_tools`` convention) - such actors pass this gate and are
    fully decided by task authority instead."""
    if not actor.allowed_tools:
        return True
    namespace = action.split(".", 1)[0]
    return "*" in actor.allowed_tools or namespace in actor.allowed_tools or action in actor.allowed_tools


def evaluate_authority(
    store: LeaseStore, actor: Actor, *, task: str, action: str, resource: str,
    impact: str = "reversible", consume: bool = True,
    lease_ids: frozenset[str] | None = None,
) -> AuthorityDecision:
    """Evaluate atomically under the store's admission lock.

    ``impact`` is caller-declared and checked against the lease's
    ``maximum_impact`` ceiling before any use is spent. ``consume=False`` is a
    non-consuming preview (the MCP gateway reserves a use itself only on an
    admitted ALLOW); ``lease_ids`` restricts evaluation to a trusted binding.
    """
    with store.transaction():
        return _evaluate_authority(store, actor, task=task, action=action, resource=resource,
                                   impact=impact, consume=consume, lease_ids=lease_ids)


def _evaluate_authority(
    store: LeaseStore, actor: Actor, *, task: str, action: str, resource: str,
    impact: str, consume: bool, lease_ids: frozenset[str] | None,
) -> AuthorityDecision:
    decision_id = f"az_{uuid.uuid4().hex[:12]}"

    if not _capability_covers(actor, action):
        return AuthorityDecision(
            decision=DecisionAction.DENY,
            reason=AuthorityReason.ACTION_OUTSIDE_CAPABILITY_MANIFEST,
            decision_id=decision_id,
        )

    subject = actor.agent_id or actor.user_id
    leases = store.for_subject_task(subject, task, actor.tenant)
    if lease_ids is not None:
        leases = [lease for lease in leases if lease.id in lease_ids]
    if not leases:
        return AuthorityDecision(
            decision=DecisionAction.DENY, reason=AuthorityReason.NO_ACTIVE_LEASE,
            decision_id=decision_id,
        )

    non_revoked = [lease for lease in leases if not lease.revoked]
    if not non_revoked:
        return AuthorityDecision(
            decision=DecisionAction.DENY, reason=AuthorityReason.LEASE_REVOKED,
            decision_id=decision_id,
        )

    now = datetime.now(UTC)
    active = [lease for lease in non_revoked if lease.expires_at is None or lease.expires_at > now]
    if not active:
        return AuthorityDecision(
            decision=DecisionAction.DENY, reason=AuthorityReason.LEASE_EXPIRED,
            decision_id=decision_id,
        )

    # Protection is a deny override across all active matching grants, independent
    # of insertion order. No use may be consumed before checking this override.
    for lease in active:
        if resource_matches(lease.resources, resource) and resource_matches(lease.protected_resources, resource):
            return AuthorityDecision(decision=DecisionAction.DENY,
                                     reason=AuthorityReason.RESOURCE_PROTECTED,
                                     lease_id=lease.id, decision_id=decision_id)

    best_reason = AuthorityReason.RESOURCE_OUTSIDE_DELEGATED_SCOPE
    for lease in active:
        if not resource_matches(lease.resources, resource):
            continue
        if action not in lease.actions:
            best_reason = AuthorityReason.ACTION_NOT_AUTHORIZED
            continue
        # Unknown impact values rank as irreversible (fail closed), same
        # convention as lease_attenuation_errors. Checked before a use is spent.
        if IMPACT_RANK.get(impact, 1) > IMPACT_RANK.get(lease.maximum_impact, 1):
            best_reason = AuthorityReason.ACTION_IMPACT_EXCEEDS_LEASE
            continue
        limit = lease.max_uses.get(action)
        available = (store.try_consume(lease.id, action, limit) if consume else
                     limit is None or store.use_count(lease.id, action) < limit)
        if not available:
            best_reason = AuthorityReason.ACTION_LIMIT_EXCEEDED
            continue

        if action in lease.require_approval:
            return AuthorityDecision(
                decision=DecisionAction.APPROVAL_REQUIRED,
                reason=AuthorityReason.ACTION_REQUIRES_APPROVAL,
                lease_id=lease.id, decision_id=decision_id,
            )
        return AuthorityDecision(
            decision=DecisionAction.ALLOW,
            reason=AuthorityReason.ACTION_WITHIN_TASK_AUTHORITY,
            lease_id=lease.id, decision_id=decision_id,
        )

    return AuthorityDecision(decision=DecisionAction.DENY, reason=best_reason, decision_id=decision_id)
