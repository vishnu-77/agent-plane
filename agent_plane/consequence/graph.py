"""Typed causal edges between resources, and the paths they trace.

``ConsequenceCatalog.dependents_of()`` already answers "what could this
resource's change eventually touch" - a flat, structural, resource-only
graph (``dependents``/``depends_on`` in ``config/resources.yaml``). That
stays the default and needs nothing new declared to keep working exactly as
today.

``transitions:`` is a separate, optional, more precise overlay for when
*why* a resource is reachable matters, not just *that* it is: "``git.push``
on ``main`` triggers CI, which enables a production deploy" is a causal
statement about this specific action, not a fact about the resource graph
in general. Deterministic and operator-declared, like everything else in
this package - no scoring, no inference, no model in the path. A catalog
that declares no ``transitions:`` produces no paths; nothing about
``evaluate()``'s existing behaviour changes.
"""
from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from agent_plane.consequence.catalog import ConsequenceCatalog

# A small, closed vocabulary on purpose - enough to say *how* one resource
# leads to another without inventing an ontology nobody will maintain.
Relation = Literal["triggers", "enables", "affects", "propagates_to", "depends_on"]


class Transition(BaseModel):
    """One causal edge: performing ``action`` (or anything, if unset) against
    a resource matching ``from_`` makes ``to`` reachable."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    action: str = "*"
    to: str
    relation: Relation
    # Only traversable once this fact is confirmed in the acting task's
    # accumulated state (agent_plane.consequence.state, not built yet as of
    # this edge existing) - unset means unconditional. Defaults closed, not
    # open: a fact-gated edge that state never wires in stays unreachable,
    # never a false ALLOW.
    requires_task_fact: str | None = None
    # "catalog" is the only source that exists yet - operator-declared,
    # deterministic, and the only kind a binding decision may use. "observed"/
    # "inferred" are reserved for a future, explicitly non-binding source (a
    # suggestion to review, never itself a reason to deny).
    source: str = "catalog"
    binding: bool = True


class ConsequencePath(BaseModel):
    """One traced chain from an entry action to where it stops reaching
    anything new (a terminal resource, or the depth bound)."""

    source_action: str
    steps: list[str]              # resource patterns, root first
    relations: list[Relation]     # len(steps) - 1
    terminal: str
    depth: int
    binding: bool


def reachable_paths(
    catalog: "ConsequenceCatalog", action: str, resource: str, *,
    task_facts: frozenset[str] = frozenset(), max_depth: int = 4,
) -> list[ConsequencePath]:
    """Causal paths from ``resource`` given ``action`` was just taken.

    The entry hop is action-conditioned: a transition only starts a path if
    both its ``from_`` and its ``action`` glob match. Every hop after that
    matches on resource alone - what a downstream system does on its own
    (CI running, a deploy pipeline firing) isn't something the calling agent
    did, the same way ``dependents_of()`` treats every hop past the first.
    A transition gated by ``requires_task_fact`` is only traversable when
    that fact is present in ``task_facts``.
    """
    transitions = getattr(catalog, "transitions", [])
    if not transitions:
        return []

    def usable(t: Transition) -> bool:
        return t.requires_task_fact is None or t.requires_task_fact in task_facts

    complete: list[ConsequencePath] = []

    def extend(steps: list[str], relations: list[Relation], bindings: list[bool], at_entry: bool) -> None:
        current = steps[-1]
        if at_entry:
            candidates = [t for t in transitions if usable(t)
                         and fnmatch.fnmatchcase(current, t.from_)
                         and fnmatch.fnmatchcase(action, t.action)]
        else:
            candidates = [t for t in transitions if usable(t)
                         and fnmatch.fnmatchcase(current, t.from_)]
        # Never step back into a resource already on this path (cycle-safe),
        # and never grow a single path past max_depth.
        candidates = [t for t in candidates if t.to not in steps]
        if not candidates or len(steps) - 1 >= max_depth:
            if not at_entry:
                complete.append(ConsequencePath(
                    source_action=action, steps=list(steps), relations=list(relations),
                    terminal=steps[-1], depth=len(steps) - 1, binding=all(bindings) if bindings else True,
                ))
            return
        for t in candidates:
            extend([*steps, t.to], [*relations, t.relation], [*bindings, t.binding], at_entry=False)

    extend([resource], [], [], at_entry=True)
    return complete
