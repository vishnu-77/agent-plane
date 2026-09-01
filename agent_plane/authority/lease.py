"""The AuthorityLease object: task-bound authority.

A capability manifest (``Actor.allowed_tools``) answers *what can this agent
technically do*. A lease answers *what is this agent authorised to do, right
now, for this task* - a narrower, expiring, resource-scoped grant. An action
is authorized only where the two intersect; see
:func:`agent_plane.authority.evaluator.evaluate_authority`.
"""
from __future__ import annotations

import fnmatch
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AuthorityLease(BaseModel):
    id: str
    task: str
    subject: str  # the agent id this lease was issued to

    resources: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)

    # Always denied even if a resource/action pair above would otherwise match -
    # e.g. a lease scoped to "github://acme/*" with "main" carved out.
    protected_resources: list[str] = Field(default_factory=list)
    # Per-action use cap (e.g. {"branch.delete": 5}). Unset = unlimited.
    max_uses: dict[str, int] = Field(default_factory=dict)
    # Actions that are in scope but still require a human in the loop.
    require_approval: list[str] = Field(default_factory=list)

    expires_at: datetime | None = None
    maximum_impact: str = "reversible"        # reversible | irreversible
    child_authority: str = "subset_only"       # "subset_only" | "none"


def resource_matches(patterns: list[str], resource: str) -> bool:
    """A resource is in scope if it glob-matches any pattern (stdlib fnmatch)."""
    return any(fnmatch.fnmatchcase(resource, p) for p in patterns)


_IMPACT_RANK = {"reversible": 0, "irreversible": 1}


def lease_attenuation_errors(parent: "AuthorityLease", child: "AuthorityLease") -> list[str]:
    """Return reasons ``child`` exceeds what ``parent`` may delegate (empty = OK).

    Mirrors ``agent_plane.gateway.a2a.attenuation_errors`` - a child lease must
    never grant more than its parent holds.
    """
    errors: list[str] = []
    extra_actions = [a for a in child.actions if a not in parent.actions]
    if extra_actions:
        errors.append(f"actions not held by parent lease: {extra_actions}")
    extra_resources = [r for r in child.resources if not resource_matches(parent.resources, r)]
    if extra_resources:
        errors.append(f"resources outside parent lease scope: {extra_resources}")
    for action, limit in child.max_uses.items():
        parent_limit = parent.max_uses.get(action)
        if parent_limit is not None and limit > parent_limit:
            errors.append(f"max_uses[{action}]={limit} exceeds parent limit {parent_limit}")
    # Unknown impact values rank as irreversible (fail closed).
    if _IMPACT_RANK.get(child.maximum_impact, 1) > _IMPACT_RANK.get(parent.maximum_impact, 1):
        errors.append(
            f"maximum_impact exceeds parent ({child.maximum_impact} > {parent.maximum_impact})"
        )
    if parent.expires_at is not None and (
        child.expires_at is None or child.expires_at > parent.expires_at
    ):
        errors.append("expires_at exceeds parent lease expiry")
    return errors


def _parse_dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def parse_lease(doc: dict[str, Any]) -> AuthorityLease:
    """Parse a lease from either shape:

    - the full manifest (``apiVersion``/``kind``, nested ``metadata``/``subject``/
      ``authority``/``constraints``/``consequence``/``delegation`` - as shipped in
      ``config/leases.yaml``), or
    - a flat dict (as posted to ``POST /v1/leases``).

    Raises ``ValueError`` on a missing required field.
    """
    meta = doc.get("metadata") or {}
    subj = doc.get("subject")
    subject = subj.get("agent") if isinstance(subj, dict) else (subj or doc.get("agent"))
    auth = doc.get("authority") or {}
    constraints = doc.get("constraints") or {}
    consequence = doc.get("consequence") or {}
    delegation = doc.get("delegation") or {}

    lease_id = meta.get("id") or doc.get("id")
    task = meta.get("task") or doc.get("task")
    if not lease_id or not task or not subject:
        raise ValueError("a lease requires 'id', 'task', and 'subject' (agent)")

    return AuthorityLease(
        id=lease_id,
        task=task,
        subject=subject,
        resources=auth.get("resources") or doc.get("resources") or [],
        actions=auth.get("actions") or doc.get("actions") or [],
        protected_resources=constraints.get("protected_resources")
        or doc.get("protected_resources") or [],
        max_uses=constraints.get("max_uses") or doc.get("max_uses") or {},
        require_approval=constraints.get("require_approval")
        or doc.get("require_approval") or [],
        expires_at=_parse_dt(constraints.get("expires_at") or doc.get("expires_at")),
        maximum_impact=consequence.get("maximum_impact")
        or doc.get("maximum_impact") or "reversible",
        child_authority=delegation.get("child_authority")
        or doc.get("child_authority") or "subset_only",
    )
