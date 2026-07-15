# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); this project uses [SemVer](https://semver.org/).

## [Unreleased]

### Added
- **Packaging:** `agent_plane` package, `agentplane` console CLI (`serve`, `init`,
  `version`, `identity`), wheel, and a hardened multi-stage non-root Docker image.
- **Identity:** `IDENTITY_MODE=delegation` — verify Ed25519-signed, scoped,
  revocable delegations; live revocation via the admin API.
- **Edges:** tool broker (`POST /v1/tools/invoke`) and RAG authorization edge
  (`POST /v1/retrieve` — identity-aware retrieval, per-document ABAC/ACL filter
  applied before generation: *relevance is not permission*), both reusing the same
  engine, identity, and signed audit chain as the model edge.
- **Agent-to-agent (A2A) edge** (`POST /v1/agents/delegate`): scoped delegation
  hand-offs that mint an attenuated Ed25519 child credential (child scope ⊆ parent
  scope — privilege escalation refused), preserving the human principal and the
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
- Derived data classification — the caller cannot downgrade a label.
- Tool least-privilege (`allowed_tools`) enforced; improvised tool calls stripped.

## [0.1.0]
- Initial control-plane MVP: OpenAI-compatible governed flow (identity →
  deterministic policy → guardrails → routing → audit).
