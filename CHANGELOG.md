# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); this project uses [SemVer](https://semver.org/).

## [Unreleased]

## [0.5.0] - 2026-09-12

### Added
- **Durable, shared authority store** (`AUTHORITY_STORE=sql`, default): leases,
  per-action use counters, approvals, and the MCP request-key ledger persist in
  the audit database (SQLite or PostgreSQL), keyed by tenant. Use reservation is one atomic
  `UPDATE ... WHERE count < limit`; PostgreSQL admission holds a transaction-scoped
  advisory lock. Multiple workers and replicas are supported; a revocation reaches
  all of them. YAML leases are seeded once and stored state wins on restart.
  `memory` keeps the previous single-process behaviour and is refused in production.
- **Approval loop**: an APPROVAL REQUIRED decision creates a tracked request
  (`GET /v1/approvals`, `POST /v1/approvals/{id}/approve|reject`), the executor
  resumes with `"approval": "<id>"` on `/v1/authorize` and receives ALLOW /
  `ACTION_APPROVED` exactly once. Bound to the exact task/action/resource,
  expires with `APPROVAL_TTL_SECONDS` and never outlives the lease; a revoked or
  expired lease wins over a granted approval. Optional signed webhook
  (`APPROVAL_WEBHOOK_URL`, HMAC-SHA256 in `X-AgentPlane-Signature`). The MCP
  gateway raises the same requests and resumes via `_meta["agent-plane/approval-id"]`.
  The operator console gains an **Approvals** page with approve/reject.
- **Lease templates**: `config/lease-templates.yaml`, `GET /v1/lease-templates`,
  `POST /v1/leases/from-template`. Variables fill `{placeholders}` and are
  restricted so a caller cannot widen scope with globs or traversal.
- **Provenance context**: optional `context` map on `/v1/authorize`
  (`parent_evidence_id`, `prompt_hash`, `conversation_id`, ...), validated, stored
  on the signed audit record as `agent-plane.provenance.v1`, echoed in the
  response, and kept on the audit event shown in the console's Decisions inspector.
- **Python SDK**: `AgentPlane.authorize(approval=, context=)`, `get_approval`,
  `wait_for_approval`, `delegate`; new `AgentPlaneAdmin` (issue/template/get/
  shrink/revoke leases, approval queue, audit); `agentplane.adapters` (`govern`
  decorator, `governed_dispatch`, `langchain_tool`/`crewai_tool`,
  `openai_agents_guard`); `agentplane.testing.check_executor` conformance kit.
- **TypeScript SDK** (`sdk/typescript`, `@agent-plane/sdk`): same contract,
  zero dependencies, `govern` wrapper, admin client.
- **MCP gateway**: production-capable; shared request deduplication;
  `agentplane mcp discover` generates a reviewable mapping file from an upstream.
- **Operations**: `/metrics` (Prometheus text), `LOG_FORMAT=json`, readiness
  checks the authority store, Helm chart and plain Kubernetes manifests under
  `deploy/`, `Dockerfile.alpine`.
- **MCP gateway** (`/mcp`, `MCP_GATEWAY_FILE`): official MCP SDK server/client pair,
  operator tool mappings with schema validation, trusted (tenant, agent) -> task/lease
  bindings, admission before dispatch with a separate upstream credential, bounded
  requests/responses, dispatch/completion/unknown-outcome receipts, and the `/flow`
  guided page (`examples/mcp_gateway_demo.py`).
- **Packaged Python SDK** (`sdk/python`, `agent-plane-sdk`): built and smoke-tested
  independently of the server; `examples/smoke_distribution.py`,
  `examples/smoke_container.py`, and `examples/check_release_version.py` gate releases.
- **Docs**: restructured under `docs/` (quickstart, per-edge integration guides,
  approvals, adapters, conformance, API reference with committed `openapi.json`,
  deployment). `integration guide.md` moved to `docs/integration/authorization.md`.

### Changed
- Docker image is a two-stage build shipping a stripped virtualenv: 368 MB ->
  287 MB with every extra (`EXTRAS` build arg trims further). The volume now
  preserves leases and approvals as well as audit records.
- `agentplane serve --workers N` is accepted with the SQL store.
- `examples/smoke_container.py` asserts lease persistence after restart.
- Lease issuance is recorded on the audit chain (`LEASE_ISSUED`).
- `production_errors()` no longer refuses `MCP_GATEWAY_FILE`; it refuses
  `AUTHORITY_STORE=memory` instead.


## [0.4.0] - 2026-09-08

### Added
- **Consequence-aware decisions** (v0.5): `POST /v1/authorize` takes an
  optional, caller-declared `impact` (`reversible` | `irreversible`,
  defaults to `reversible`) and denies (`ACTION_IMPACT_EXCEEDS_LEASE`)
  outright when it outranks the matched lease's `maximum_impact` ceiling -
  previously that field was parsed but purely informational. Checked before
  `max_uses` is consumed, so a denied-for-impact call doesn't burn a use
  slot the caller never got to spend.
- **Lease revocation + shrinking** (`DELETE`/`PATCH /v1/leases/{id}`, v0.4):
  an operator can pull or narrow an active lease's authority mid-task,
  effective immediately - no waiting for its natural expiry. `PATCH` reuses
  the delegation edge's attenuation rule (`lease_attenuation_errors`), so a
  shrink can only narrow, never widen; that shared validator also now
  refuses to drop a parent's `require_approval`/`protected_resources`
  safeguard on delegation, closing a gap the v0.3 delegate endpoint had
  (an explicit non-empty `require_approval` override could previously omit
  an action the parent required approval for). Admin-token gated and
  audited, same as lease issuance.
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
- **`INTEGRATION.md`**: an integration guide describing setup and the
  application changes required for each interface.

### Fixed
- **`LeaseStore` was not tenant-partitioned:** two tenants sharing an
  `agent_id` + task string could collide and share leases/usage counters.
  `AuthorityLease` gets a `tenant` field; lookup and evaluation now filter
  by it, and delegated child leases carry the parent's tenant rather than
  the delegating actor's own claim.
- **Fail-closed on an empty policy bundle in production:** a `POLICY_DIR`
  whose files all parse to zero policies logged a warning but still started
  in `ENVIRONMENT=production`, running allow-all. Now refuses to start.
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
