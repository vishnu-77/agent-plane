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
- rename or remove `allowed_tools` (every existing caller keeps working
  unchanged — see PR-2 in the implementation plan for the full caller
  audit),
- change `_capability_covers` or any evaluator behavior,
- add a new authorization check.

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
