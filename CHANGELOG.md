# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); this project uses [SemVer](https://semver.org/).

## [Unreleased]

### Added
- **Lease delegation** (`POST /v1/leases/{id}/delegate`, v0.3): the lease
  holder mints an attenuated child `AuthorityLease` - self-service, mirroring
  the A2A identity edge's attenuation (child scope must be a subset of the
  parent's resources/actions/max_uses/impact/expiry), gated by the parent
  lease's `child_authority` field (a child defaults to `child_authority: none`
  so re-delegation doesn't chain unbounded unless explicitly granted).
- **Task-authority edge** (`POST /v1/authorize`): decides whether a specific
  proposed action on a specific resource is authorised for the agent's current
  *task* - distinct from what it's generically capable of (`Actor.allowed_tools`).
  New `AuthorityLease` object (`agent_plane/authority/`, config-driven via
  `config/leases.yaml`, issuable at runtime via `POST /v1/leases`): resource
  scope, protected-resource carve-outs, per-action use limits, expiry, and
  approval gating. Same identity layer and signed audit chain as every other
  edge. See `spec/authority-lease.md`.
- **Python SDK** (`sdk/python/agentplane`): minimal HTTP client for
  `authorize()`.
- **`examples/devops-agent/demo.py`** and **`examples/verify_deployment.py`**:
  a runnable capability-vs-authority demo and a live-deployment smoke test
  covering every edge.
- **`INTEGRATION.md`** and **`ROADMAP.md`**: a plug-and-play integration guide
  (with an honest accounting of what is and isn't zero-code today) and the
  staged v0.1-v1.0 plan.

### Fixed
- The tool broker (`POST /v1/tools/invoke`) and RAG edge (`POST /v1/retrieve`)
  now apply the `redact` obligation to tool arguments/results and retrieved
  document text - previously only the model-completion edge redacted.
- Admin mutations (revoke, un-revoke, policy hot-reload) are now recorded in
  the same signed audit chain as every other decision, instead of being a
  blind spot for a caller holding `ADMIN_TOKEN`.

## [0.2.0] - 2026-07-30

### Added
- **Packaging:** `agent_plane` package, `agentplane` console CLI (`serve`, `init`,
  `version`, `identity`), wheel, and a hardened multi-stage non-root Docker image.
- **Identity:** `IDENTITY_MODE=delegation` - verify Ed25519-signed, scoped,
  revocable delegations; live revocation via the admin API.
- **Edges:** tool broker (`POST /v1/tools/invoke`) and RAG authorization edge
  (`POST /v1/retrieve` - identity-aware retrieval, per-document ABAC/ACL filter
  applied before generation: *relevance is not permission*), both reusing the same
  engine, identity, and signed audit chain as the model edge.
- **Agent-to-agent (A2A) edge** (`POST /v1/agents/delegate`): scoped delegation
  hand-offs that mint an attenuated Ed25519 child credential (child scope ⊆ parent
  scope - privilege escalation refused), preserving the human principal and the
  delegation chain, fully audited + revocable.
- **Usage metering:** per-tenant metering of model + tool calls; `GET /v1/usage`
  with optional `config/pricing.yaml` cost estimate (metering, not billing).
- **Admin API:** live revocation and policy hot-reload (token-guarded).
- **Config-driven:** model catalog (`config/models.yaml`) and tool catalog
  (`config/tools.yaml`); `agentplane init` scaffolds defaults.
- **Production readiness:** fail-closed startup in `ENVIRONMENT=production`,
  bundled default policies (no silent allow-all), request IDs, structured logging,
  global error handler, CORS config, `/readyz`, and CI.

### Security
- Tamper-evident audit: hash-chained + HMAC-signed events (`verify_chain`),
  with **serialized appends** (in-process lock + Postgres advisory lock) and a
  unique `event_hash` so the chain cannot fork or duplicate under concurrency.
- `GET /v1/audit` is admin-only; admin token compared in constant time.
- Abuse protection: request-size cap (413) and per-client rate limiting (429).
- Derived data classification - the caller cannot downgrade a label.
- Tool least-privilege (`allowed_tools`) enforced; improvised tool calls stripped.

## [0.1.0]
- Initial control-plane MVP: OpenAI-compatible governed flow (identity →
  deterministic policy → guardrails → routing → audit).
