"""The Authority Evaluator: decides ALLOW / DENY / APPROVAL_REQUIRED for one
proposed (task, action, resource), pre-execution.

Two independent gates, in order:

1. **Capability** - does the actor's capability manifest (``Actor.allowed_tools``)
   cover this action's namespace at all? This is the identity layer's static
   grant, same one the tool broker enforces.
2. **Task authority** - do any of the actor's active :class:`AuthorityLease`
   grants for this *task* cover this *resource* and *action*, under their
   constraints (protected resources, use limits, expiry)?

Capability without task authority is exactly the "agent holds
github.delete_repository but this task only authorises branch cleanup on one
repo" case - denied at gate 2, not gate 1.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel

from agent_plane.authority.lease import resource_matches
from agent_plane.authority.store import LeaseStore
from agent_plane.schemas.canonical import Actor, DecisionAction


class AuthorityReason(str, Enum):
    ACTION_OUTSIDE_CAPABILITY_MANIFEST = "ACTION_OUTSIDE_CAPABILITY_MANIFEST"
    NO_ACTIVE_LEASE = "NO_ACTIVE_LEASE"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    RESOURCE_OUTSIDE_DELEGATED_SCOPE = "RESOURCE_OUTSIDE_DELEGATED_SCOPE"
    RESOURCE_PROTECTED = "RESOURCE_PROTECTED"
    ACTION_NOT_AUTHORIZED = "ACTION_NOT_AUTHORIZED"
    ACTION_LIMIT_EXCEEDED = "ACTION_LIMIT_EXCEEDED"
    ACTION_WITHIN_TASK_AUTHORITY = "ACTION_WITHIN_TASK_AUTHORITY"
    ACTION_REQUIRES_APPROVAL = "ACTION_REQUIRES_APPROVAL"


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
    store: LeaseStore, actor: Actor, *, task: str, action: str, resource: str
) -> AuthorityDecision:
    decision_id = f"az_{uuid.uuid4().hex[:12]}"

    if not _capability_covers(actor, action):
        return AuthorityDecision(
            decision=DecisionAction.DENY,
            reason=AuthorityReason.ACTION_OUTSIDE_CAPABILITY_MANIFEST,
            decision_id=decision_id,
        )

    subject = actor.agent_id or actor.user_id
    leases = store.for_subject_task(subject, task)
    if not leases:
        return AuthorityDecision(
            decision=DecisionAction.DENY, reason=AuthorityReason.NO_ACTIVE_LEASE,
            decision_id=decision_id,
        )

    now = datetime.now(UTC)
    active = [l for l in leases if l.expires_at is None or l.expires_at > now]
    if not active:
        return AuthorityDecision(
            decision=DecisionAction.DENY, reason=AuthorityReason.LEASE_EXPIRED,
            decision_id=decision_id,
        )

    best_reason = AuthorityReason.RESOURCE_OUTSIDE_DELEGATED_SCOPE
    for lease in active:
        if not resource_matches(lease.resources, resource):
            continue
        # A protected resource is denied outright - it cannot be reached via a
        # different, more permissive lease for the same task.
        if resource_matches(lease.protected_resources, resource):
            return AuthorityDecision(
                decision=DecisionAction.DENY, reason=AuthorityReason.RESOURCE_PROTECTED,
                lease_id=lease.id, decision_id=decision_id,
            )
        if action not in lease.actions:
            best_reason = AuthorityReason.ACTION_NOT_AUTHORIZED
            continue
        if not store.try_consume(lease.id, action, lease.max_uses.get(action)):
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
