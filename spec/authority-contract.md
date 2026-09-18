# Authority Contract

Status: implemented (0.8.x). `agent_plane/authority/contract.py`
(`AuthorityContract`, `compile_contract`, `parse_contract`,
`ContractRegistry`) and `agent_plane/gateway/contracts.py`
(`POST`/`GET /v1/contracts`, `.../history`, `.../compile`).

## What it is

A human-readable, versioned policy document that *compiles* into the
existing `AuthorityLease`/rules machinery, rather than replacing it:

```text
Authority Contract
       │ compile
       ▼
AuthorityLease
       │
       ▼
   Evaluator (agent_plane/authority/evaluator.py — unchanged)
```

The evaluator, `AuthorityLease` shape, and evaluation order documented in
`spec/authority-lease.md` are **not** changed by this — an Authority
Contract is a second, friendlier surface that compiles down to the lease
shape that already exists, the same relationship `spec/authority-lease.md`
already describes between a YAML lease document and the flat dict
`POST /v1/leases` accepts. `compile_contract()` is a pure function; nothing
about it is evaluated except the `AuthorityLease` it produces.

## Shape

```yaml
contract_id: claude-code-coding
agent: claude-code

allow: [filesystem.read, filesystem.write, tests.execute]
ask_first: [git.push]
never: [repository.delete, secrets.read]

resources:
  allow: [workspace/**]
  protected: [workspace/.env*, production/**]

consequence:
  environments: [development]
  max_reversibility: [reversible]

delegation:
  children: subset_only
```

`parse_contract()` accepts exactly this shape. A flat `AuthorityContract`
document (as `POST /v1/contracts` also accepts) uses the same field names
without the nested `resources`/`consequence`/`delegation` grouping.

## Versioning (Phase 14)

Every contract carries `contract_id`/`version`/`created_by`/`approved_by`/
`created_at`/`effective_at`/`supersedes`, plus a `fingerprint` - a
deterministic hash of the *governance content* only (not bookkeeping
fields), so two contracts with identical policy share a fingerprint
regardless of who authored them or when. `ContractRegistry.at(tenant,
contract_id, as_of=...)` answers "which policy version was effective on
`<date>`" exactly - the GRC question this phase exists to answer.

## Domain packs build on this

`agent_plane/domains/{software,cloud}.py`'s `suggested_contract()` returns
an `AuthorityContract`, proving the same compiler serves both a
hand-authored contract and a domain pack's generated starter policy - see
`spec/trust-domains.md`'s sibling concept and `agent_plane/domains/base.py`.

## Deliberately not built here

- A UI (Phase 15 - the human-readable console rendering of a contract, and
  a "view compiled lease" toggle for security engineers). This module is
  API/model-only.
- An approval workflow beyond the `approved_by` field existing to be set.
- Contract persistence beyond the in-memory `ContractRegistry`
  (`# ponytail:` noted in `contract.py` - add a `ContractRow`, mirroring
  `AgentDefinitionRow` in `registry/store.py`, when an operator needs
  contracts to survive a restart).
