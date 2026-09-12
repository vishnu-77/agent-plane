"""The AuthorityLease object: task-bound authority.

A capability manifest (``Actor.allowed_tools``) answers *what can this agent
technically do*. A lease answers *what is this agent authorised to do, right
now, for this task* - a narrower, expiring, resource-scoped grant. An action
is authorized only where the two intersect; see
:func:`agent_plane.authority.evaluator.evaluate_authority`.
"""
from __future__ import annotations

import fnmatch
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class AuthorityLease(BaseModel):
    id: str
    task: str
    subject: str  # the agent id this lease was issued to
    tenant: str = "default"  # matches Actor.tenant's default; scopes for_subject_task lookups

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
    revoked: bool = False

    # --- lineage and consequence (0.6) ---
    # The lease this one was attenuated from (set by delegation). Walking
    # parent_lease upwards yields the authority lineage back to its origin.
    parent_lease: str | None = None
    # Where the authority came from: {"kind": "human"|"prompt"|"event"|"parent"|"api",
    # "ref": "...", "created_by": "...", "text": "..."}. Provenance, not permission.
    origin: dict[str, Any] = Field(default_factory=dict)
    # Bounds on what an authorised action may *cause* (see agent_plane.consequence):
    #   max_impact: none|low|medium|high|critical   (default: follows maximum_impact)
    #   environments: [..]        only these environments may be mutated
    #   customer_facing: bool     may mutate customer-facing resources (default true)
    #   max_reversibility: reversible|recoverable|irreversible (default irreversible)
    #   max_blast_radius: int     max resources affected incl. downstream
    permitted_consequence: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expires_at")
    @classmethod
    def _tz_aware(cls, value: datetime | None) -> datetime | None:
        """A naive expiry (e.g. `expires_at: "2027-01-01T00:00:00"` in a YAML
        manifest) would raise TypeError when the evaluator compares it to an
        aware `datetime.now(UTC)`. Normalize on the way in, once, so no
        consumer has to defend against it."""
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


def resource_matches(patterns: list[str], resource: str) -> bool:
    """A resource is in scope if it glob-matches any pattern (stdlib fnmatch)."""
    return any(fnmatch.fnmatchcase(resource, p) for p in patterns)


# Shared with evaluator.py, which gates a proposed action's declared impact
# against a lease's `maximum_impact` ceiling the same way this ranks a child
# lease's ceiling against its parent's.
IMPACT_RANK = {"reversible": 0, "irreversible": 1}


def lease_attenuation_errors(parent: AuthorityLease, child: AuthorityLease) -> list[str]:
    """Return reasons ``child`` exceeds what ``parent`` may delegate (empty = OK).

    Mirrors ``agent_plane.gateway.a2a.attenuation_errors`` - a child lease must
    never grant more than its parent holds. Also used to validate an in-place
    lease *shrink* (``parent`` = the lease's current values, ``child`` = the
    requested narrower values) - the same "never grants more" rule applies.
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
    if IMPACT_RANK.get(child.maximum_impact, 1) > IMPACT_RANK.get(parent.maximum_impact, 1):
        errors.append(
            f"maximum_impact exceeds parent ({child.maximum_impact} > {parent.maximum_impact})"
        )
    if parent.expires_at is not None and (
        child.expires_at is None or child.expires_at > parent.expires_at
    ):
        errors.append("expires_at exceeds parent lease expiry")
    # A narrower lease may not quietly drop safeguards the parent already had.
    unapproved = [
        a for a in child.actions if a in parent.require_approval and a not in child.require_approval
    ]
    if unapproved:
        errors.append(f"drops required-approval on actions: {unapproved}")
    unprotected = [p for p in parent.protected_resources if p not in child.protected_resources]
    if unprotected:
        errors.append(f"removes protected resources: {unprotected}")
    errors.extend(consequence_attenuation_errors(parent.permitted_consequence, child.permitted_consequence))
    return errors


_CONSEQUENCE_IMPACT = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_CONSEQUENCE_REVERSIBILITY = {"reversible": 0, "recoverable": 1, "irreversible": 2}


def consequence_attenuation_errors(parent: dict[str, Any], child: dict[str, Any]) -> list[str]:
    """A child's permitted consequence may only be narrower than its parent's."""
    errors: list[str] = []
    if "max_impact" in parent:
        child_impact = child.get("max_impact", "critical")
        if _CONSEQUENCE_IMPACT.get(child_impact, 4) > _CONSEQUENCE_IMPACT.get(parent["max_impact"], 4):
            errors.append(f"permitted_consequence.max_impact {child_impact} exceeds parent {parent['max_impact']}")
    if "environments" in parent:
        child_envs = child.get("environments")
        if child_envs is None or any(e not in parent["environments"] for e in child_envs):
            errors.append("permitted_consequence.environments widens the parent's environments")
    if parent.get("customer_facing") is False and child.get("customer_facing", True) is not False:
        errors.append("permitted_consequence.customer_facing widens the parent's")
    if "max_reversibility" in parent:
        child_rev = child.get("max_reversibility", "irreversible")
        if _CONSEQUENCE_REVERSIBILITY.get(child_rev, 2) > _CONSEQUENCE_REVERSIBILITY.get(parent["max_reversibility"], 2):
            errors.append("permitted_consequence.max_reversibility widens the parent's")
    if "max_blast_radius" in parent:
        child_blast = child.get("max_blast_radius")
        if child_blast is None or int(child_blast) > int(parent["max_blast_radius"]):
            errors.append("permitted_consequence.max_blast_radius widens the parent's")
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

    tenant = (subj.get("tenant") if isinstance(subj, dict) else None) \
        or meta.get("tenant") or doc.get("tenant") or "default"

    return AuthorityLease(
        id=lease_id,
        task=task,
        subject=subject,
        tenant=tenant,
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
        parent_lease=delegation.get("parent_lease") or doc.get("parent_lease"),
        origin=dict(meta.get("origin") or doc.get("origin") or {}),
        permitted_consequence=dict(
            consequence.get("permitted") or doc.get("permitted_consequence") or {}
        ),
    )
