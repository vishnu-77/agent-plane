# Capability ≠ Authority

Status: terminology freeze (PR-1). This doc does not introduce a new
distinction — it names one that already exists in code and quotes it, so
there is exactly one source of truth instead of two drifting descriptions.

## The existing statement

From `spec/authority-lease.md:3`:

> **Capability ≠ Authority.** `Actor.allowed_tools` (the capability manifest,
> resolved from identity) says what an agent can *technically* do. An
> `AuthorityLease` says what it's *authorised* to do, right now, for one
> task. A proposed action is only allowed where both agree - see
> `agent_plane/authority/evaluator.py`.

From `agent_plane/authority/evaluator.py`'s module docstring (lines 1-17):
the capability gate (`_capability_covers`, `evaluator.py:78-81`) is checked
first and independently of the lease/authority checks that follow it — the
two are already separate gates in the evaluation order documented in
`spec/authority-lease.md` § "Evaluation order".

## What PR-2 changes, and what it doesn't

PR-2 adds `Actor.capabilities` as a read-only property alias for
`Actor.allowed_tools` (`agent_plane/schemas/canonical.py`). This gives the
concept quoted above a name that matches the prose ("capability manifest")
instead of a field name (`allowed_tools`) that reads like a permission grant
to someone encountering it for the first time.

It does **not**:
- rename or remove `allowed_tools` as a field, wire format, or constructor
  kwarg — every external client and every `Actor(allowed_tools=...)`
  construction site keeps working unchanged,
- add a new authorization check.

Update (0.8): the read site that matters most for this doc's own point —
`agent_plane/authority/evaluator.py`'s `_capability_covers`, the capability
gate's sole implementation — now reads `actor.capabilities` rather than
`actor.allowed_tools`. Since the property is a pure alias this changes no
behavior; it's the "internally migrate reads to `.capabilities`" half of
the roadmap's Phase 1, done at the one site where it actually matters for
readers of this doc rather than as a mechanical sweep of every read site
(`enforcement/service.py`, `policy/engine.py`, `gateway/router.py`,
`gateway/a2a.py` still read `allowed_tools` directly — a pure rename with
no behavior change, safe to do opportunistically when next touching those
files).

## Acceptance condition

No documentation in this repository should describe `allowed_tools` /
`capabilities` as itself granting permission to act. Where a doc needs to
express "what the runtime can attempt," it should read:

```text
capability_manifest (Actor.capabilities / Actor.allowed_tools)
        ∩
AuthorityLease
        =
potential executable authority
```
