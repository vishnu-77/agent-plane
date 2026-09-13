"""The bound a lease places on what an authorised action may *cause*.

Three call sites used to each implement their own version of this: a lease's
own bound was checked against a computed :class:`~agent_plane.consequence.catalog.Consequence`
(``authority.service.consequence_violations``), one lease's bound was checked
against its parent's (``authority.lease.consequence_attenuation_errors``, its
own 5-level rank tables duplicated from the catalog's), and a project's rules
were merged into one compiled lease's bound (``rules.store.compile_rules``,
which only handled ``list``/``bool``/``int`` values - the two *string*
fields, ``max_impact`` and ``max_reversibility``, fell through untouched, so
whichever applicable rule iterated first silently won).

One type, three operations, used at all three sites: :meth:`narrows` (child
vs. parent, delegation), :meth:`meet` (rule vs. rule, compilation),
:meth:`violated_by` (lease vs. computed consequence, the actual gate).

The dict shape on :class:`~agent_plane.authority.lease.AuthorityLease` (and
in rule/API payloads) is unchanged - this validates it on the way in at each
use site rather than replacing it, so no persisted lease, no YAML, and no API
response changes shape.
"""
from __future__ import annotations

import fnmatch

from pydantic import BaseModel, ConfigDict

from agent_plane.consequence.catalog import (
    IMPACT_RANK,
    PERSISTENCE_RANK,
    REVERSIBILITY_RANK,
    Consequence,
    Impact,
    Persistence,
    Reversibility,
)


class ConsequenceEnvelope(BaseModel):
    """Bounds on what an authorised action may cause. Every field is a
    ceiling: unset means unbounded on that dimension, not zero."""

    model_config = ConfigDict(extra="forbid")

    max_impact: Impact | None = None
    environments: list[str] | None = None
    customer_facing: bool | None = None
    max_reversibility: Reversibility | None = None
    max_persistence: Persistence | None = None
    max_blast_radius: int | None = None
    # The two below are inert until a catalog declares `transitions:` and a
    # Consequence carries `paths` - checked, but always trivially satisfied
    # (no paths to be outside of) when nothing declares transitions.
    max_depth: int | None = None
    allowed_terminal_resources: list[str] | None = None
    forbidden_terminal_resources: list[str] = []

    def narrows(self, parent: "ConsequenceEnvelope") -> list[str]:
        """Why ``self`` (a child) is not narrower than ``parent`` (empty = fine).

        A child's bound may only tighten its parent's; widening on any single
        dimension is rejected, even if every other dimension is unchanged or
        tighter. Mirrors ``lease_attenuation_errors``'s other checks (actions,
        resources, max_uses, ...): same "child ⊆ parent" discipline, just for
        the consequence dimensions.
        """
        errors: list[str] = []
        if parent.max_impact is not None:
            child = self.max_impact or "critical"
            if IMPACT_RANK[child] > IMPACT_RANK[parent.max_impact]:
                errors.append(f"permitted_consequence.max_impact {child} exceeds parent {parent.max_impact}")
        if parent.environments is not None:
            if self.environments is None or any(e not in parent.environments for e in self.environments):
                errors.append("permitted_consequence.environments widens the parent's environments")
        if parent.customer_facing is False and self.customer_facing is not False:
            errors.append("permitted_consequence.customer_facing widens the parent's")
        if parent.max_reversibility is not None:
            child = self.max_reversibility or "irreversible"
            if REVERSIBILITY_RANK[child] > REVERSIBILITY_RANK[parent.max_reversibility]:
                errors.append("permitted_consequence.max_reversibility widens the parent's")
        if parent.max_persistence is not None:
            child = self.max_persistence or "permanent"
            if PERSISTENCE_RANK[child] > PERSISTENCE_RANK[parent.max_persistence]:
                errors.append("permitted_consequence.max_persistence widens the parent's")
        if parent.max_blast_radius is not None:
            if self.max_blast_radius is None or self.max_blast_radius > parent.max_blast_radius:
                errors.append("permitted_consequence.max_blast_radius widens the parent's")
        if parent.max_depth is not None:
            if self.max_depth is None or self.max_depth > parent.max_depth:
                errors.append("permitted_consequence.max_depth widens the parent's")
        if parent.allowed_terminal_resources is not None:
            child = self.allowed_terminal_resources
            if child is None or any(t not in parent.allowed_terminal_resources for t in child):
                errors.append("permitted_consequence.allowed_terminal_resources widens the parent's")
        if parent.forbidden_terminal_resources:
            missing = [t for t in parent.forbidden_terminal_resources if t not in self.forbidden_terminal_resources]
            if missing:
                errors.append("permitted_consequence.forbidden_terminal_resources drops a parent bound")
        return errors

    def meet(self, other: "ConsequenceEnvelope") -> "ConsequenceEnvelope":
        """The narrowest envelope both ``self`` and ``other`` satisfy.

        Used to fold a project's applicable rules into one compiled lease:
        each rule's bound is met against the running total, so the result is
        never wider than any single rule's - unset stays unset only where
        *neither* side bounds that dimension.
        """
        def narrower_impact(a: Impact | None, b: Impact | None) -> Impact | None:
            if a is None:
                return b
            if b is None:
                return a
            return a if IMPACT_RANK[a] <= IMPACT_RANK[b] else b

        def narrower_reversibility(a: Reversibility | None, b: Reversibility | None) -> Reversibility | None:
            if a is None:
                return b
            if b is None:
                return a
            return a if REVERSIBILITY_RANK[a] <= REVERSIBILITY_RANK[b] else b

        def narrower_persistence(a: Persistence | None, b: Persistence | None) -> Persistence | None:
            if a is None:
                return b
            if b is None:
                return a
            return a if PERSISTENCE_RANK[a] <= PERSISTENCE_RANK[b] else b

        def narrower_int(a: int | None, b: int | None) -> int | None:
            if a is None:
                return b
            if b is None:
                return a
            return min(a, b)

        def narrower_list(a: list[str] | None, b: list[str] | None) -> list[str] | None:
            if a is None:
                return b
            if b is None:
                return a
            return [v for v in a if v in b]

        return ConsequenceEnvelope(
            max_impact=narrower_impact(self.max_impact, other.max_impact),
            environments=narrower_list(self.environments, other.environments),
            customer_facing=(False if False in (self.customer_facing, other.customer_facing)
                             else self.customer_facing if self.customer_facing is not None else other.customer_facing),
            max_reversibility=narrower_reversibility(self.max_reversibility, other.max_reversibility),
            max_persistence=narrower_persistence(self.max_persistence, other.max_persistence),
            max_blast_radius=narrower_int(self.max_blast_radius, other.max_blast_radius),
            max_depth=narrower_int(self.max_depth, other.max_depth),
            allowed_terminal_resources=narrower_list(self.allowed_terminal_resources, other.allowed_terminal_resources),
            forbidden_terminal_resources=sorted(set(self.forbidden_terminal_resources) | set(other.forbidden_terminal_resources)),
        )

    def violated_by(self, consequence: Consequence) -> list[str]:
        """Why ``consequence`` exceeds this envelope (empty = within bounds).

        Runs for every consequence, not only mutating ones: a read that
        exposes a credential or exports data already carries a real `impact`
        from ``catalog.evaluate()`` (see its `credential_access`/`data_egress`
        handling) and must be checked against the same bounds a write would
        be. A merely low-impact read still trivially satisfies every check
        below, so there is nothing to skip by special-casing reads.
        """
        errors: list[str] = []
        if self.max_impact and IMPACT_RANK.get(consequence.impact, 4) > IMPACT_RANK[self.max_impact]:
            errors.append(f"impact {consequence.impact} exceeds permitted {self.max_impact}")
        if self.environments is not None:
            outside = [e for e in (consequence.environments or [consequence.environment]) if e not in self.environments]
            if outside:
                errors.append(f"mutates {', '.join(outside)} but only {', '.join(self.environments)} is permitted")
        if self.customer_facing is False and consequence.customer_facing:
            errors.append("customer-facing workload is not permitted for this task")
        if self.max_reversibility and REVERSIBILITY_RANK.get(consequence.reversibility, 2) > REVERSIBILITY_RANK[self.max_reversibility]:
            errors.append(f"{consequence.reversibility} effect exceeds permitted {self.max_reversibility}")
        if self.max_blast_radius is not None and consequence.blast_radius > self.max_blast_radius:
            errors.append(f"blast radius {consequence.blast_radius} exceeds permitted {self.max_blast_radius}")
        if self.max_persistence and PERSISTENCE_RANK[consequence.persistence] > PERSISTENCE_RANK[self.max_persistence]:
            errors.append(f"{consequence.persistence} persistence exceeds permitted {self.max_persistence}")
        paths = getattr(consequence, "paths", None) or []
        if self.max_depth is not None:
            over = [p for p in paths if p.depth > self.max_depth]
            if over:
                errors.append(f"a reachable path has depth {max(p.depth for p in over)}, permitted max_depth is {self.max_depth}")
        if self.allowed_terminal_resources is not None:
            outside_terminals = [p.terminal for p in paths
                                 if not any(fnmatch.fnmatchcase(p.terminal, g) for g in self.allowed_terminal_resources)]
            if outside_terminals:
                errors.append(f"reaches {', '.join(sorted(set(outside_terminals)))}, outside the permitted terminal resources")
        if self.forbidden_terminal_resources:
            forbidden_hits = [p.terminal for p in paths
                              if any(fnmatch.fnmatchcase(p.terminal, g) for g in self.forbidden_terminal_resources)]
            if forbidden_hits:
                errors.append(f"reaches forbidden terminal resource(s) {', '.join(sorted(set(forbidden_hits)))}")
        return errors
