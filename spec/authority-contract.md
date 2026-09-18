# Authority Contract

Status: name reserved (PR-1). Not designed or built by PR-1..7 — this doc
exists only so later work doesn't invent competing terms for the same
concept.

## What it will be (future work, not this sprint)

A human-readable, versioned policy document that *compiles* into the
existing `AuthorityLease`/rules machinery, rather than replacing it:

```text
Authority Contract
       │ compile
       ▼
     Rules
       │ compile
       ▼
AuthorityLease
       │
       ▼
   Evaluator (agent_plane/authority/evaluator.py — unchanged)
```

The evaluator, `AuthorityLease` shape, and evaluation order documented in
`spec/authority-lease.md` are **not** changed by introducing this concept
later — an Authority Contract is a second, friendlier surface that compiles
down to the lease shape that already exists, the same relationship
`spec/authority-lease.md` already describes between a YAML lease document
and the flat dict `POST /v1/leases` accepts.

## Why this doc exists now

PR-1..7 touch identity, capability naming, and the registry — none of them
build an Authority Contract or its compiler. Reserving the name and the
compile-down-to-`AuthorityLease` relationship here prevents PR-3's
`agent_plane/identity/` package or PR-6/PR-7's registry work from
accidentally reinventing a parallel "policy bundle" concept under a
different name.

## Out of scope for PR-1..7

- The contract schema/YAML shape
- A compiler from contract → lease
- Versioning, approval workflow, or a UI for it

These remain future roadmap work, not part of this sprint.
