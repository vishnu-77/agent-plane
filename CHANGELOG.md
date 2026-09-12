# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); this project uses [SemVer](https://semver.org/).

## [Unreleased]

## [0.7.0] - 2026-09-12

agent-plane becomes a product a developer can adopt alone, in minutes. Sign up,
create a project, connect an agent, and activity appears. Nothing in that path
asks for a JWT, an authority lease, a policy bundle, or an admin token. The
authority engine underneath is unchanged; what changed is everything a
developer has to know before it starts working for them.

### Added
- **Accounts and tenancy** (`agent_plane/accounts`): User -> Workspace -> Project,
  with agents, sessions, and tasks discovered under a project. Passwords are
  hashed with scrypt (stdlib); the console authenticates as a human with a
  signed, httponly session cookie and holds no token in JavaScript.
  `POST /v1/auth/signup|login|logout`, `GET /v1/auth/state|me`,
  `POST /v1/auth/exchange`, and `/v1/projects*`.
- **Project API keys**: `ap_live_`, `ap_test_`, and `ap_mgmt_` keys, HMAC-SHA256
  hashed at rest and shown exactly once, listed by prefix and last four, with
  rotate and revoke per machine. `/v1/api-keys*`.
- **One ingestion edge** (`POST /v1/events/action`, `POST /v1/sessions`): every
  integration reports the same way, one event or a batch of up to 50. Tool calls
  are normalized into actions and resources (`agent_plane/events/normalize.py`),
  which also strips usernames out of absolute paths before anything is stored.
  The project's data-collection policy is applied before storage: metadata is
  kept, prompt text, tool arguments, and outputs are dropped unless a human
  turned them on.
- **Rules** (`agent_plane/rules`, `/v1/rules*`): the developer-facing primitive,
  three lists - ALLOW, ASK FIRST, NEVER - scoped by agent, integration, and
  environment. They compile into a task-scoped `AuthorityLease` with a
  fingerprint, so an edit takes effect on the next action and disabling a rule
  takes the authority away. `NEVER` is absolute: it is checked across every
  active grant before scope, limits, or approval, so no other rule, lease, or
  delegation can grant it back (`ACTION_REFUSED_BY_RULE`).
- **Suggested rules** (`GET /v1/rules/suggested`): a reviewable draft built from
  what each agent actually did - reads proposed as allowed, changes as ask-first,
  destructive actions as never. A suggestion disappears once a rule covers it.
- **Connectors** (`agent_plane/connect`): `agentplane connect claude|codex|cursor|mcp|sdk`,
  plus `status` and `disconnect`. Each verifies the key against the running
  service, stores the credential in `~/.agentplane/credentials.json` (0600,
  outside anything people commit), installs what that agent needs, and states
  what it can and cannot enforce. The Claude Code connector installs a PreToolUse
  hook that blocks only a binding refusal or a pending approval, and exits 0
  when agent-plane is unreachable.
- **Govern mode**: projects carry their own runtime mode. Observe records and
  blocks nothing (SIMULATE with `would_be`), Govern returns the real decision
  with `enforced: false`, Enforce binds. Every decision carries `binding`, which
  is true only where the connector can actually block, so the product never
  implies it stopped something it could not stop.
- **Permissions as a file** (`agent_plane/rules/yaml_io.py`): the same rules in
  YAML, for teams who keep them in version control. `GET /v1/rules/export` and
  `POST /v1/rules/import` (merge by rule name, or `replace` to make the file the
  whole truth), the **Permissions as a file** panel on the Rules screen, and
  `agentplane rules check|pull|push`. `check` needs no credential and changes
  nothing, so it belongs in the pull request that changes the file. An unknown
  field is an error rather than something quietly ignored.
- **New console** (`console/`): Activity, Agents, Rules, Integrations, and
  Settings. Activity rows show time, agent, action, resource, and result; a row
  opens a Decision Drawer with the plain answer first and the authority path on
  request. Sign-in and a two-step onboarding create the first project.

### Changed
- `POST /v1/authorize` and `/v1/tasks` accept a project API key, not only a JWT.
- A result that no rule produced is reported as observed, not as a violation. A
  project with no rules denies by default, so Observe and Govern compute "would
  be denied" for every action; that is a fact about the project, not about the
  action, and it is now stated once for the project instead of on every row.
- An out-of-scope denial no longer describes authority that does not exist: the
  explanation distinguishes an action the task grants elsewhere from one it never
  granted at all.
- The demo project is readable while signed in, so the console's LIVE/DEMO switch
  works after sign-up instead of returning 403.
- Console navigation is four items. The previous Live, Tasks, Resources, Govern,
  Decisions, Policies, Evidence, and Platform pages are removed.
- New projects default to Observe. The deployment-wide default is unchanged.
- The MCP gateway accepts the Project API Key its own connector prints, which it
  previously refused with `401 invalid_identity_or_task_binding`. Identity tokens
  still work. A key authenticates and never authorizes: the caller's
  `(tenant, agent)` pair must match a binding in `MCP_GATEWAY_FILE`, and because a
  key asserts no capability manifest, the operator's tool mapping is the manifest
  rather than anything the client declares about itself.
- Deployment-wide operations behind a console session (policy reload, credential
  revocation, lease issue and revoke, quarantine, the global mode fallback) now
  require the account that created the instance. Any signed-in account qualified
  before, which on a shared install made every developer an administrator of
  everyone else's tenants. `ADMIN_TOKEN` and management keys are unchanged.
- `GET /demo/scenarios` reports the demo project's real id (`prj_demo`) instead of
  `demo`, which matched nothing.
- The rule editor lists what coding agents actually do first, instead of the
  operator vocabulary the catalog happens to declare first.

### Fixed
- A completed MCP dispatch was recorded as `outcome_unknown` against MCP SDK
  releases that renamed `is_error` to `isError`: the result is read from the
  serialised payload, before redaction, and both spellings are accepted.
- `examples` is a real package, so an unrelated installed distribution shipping a
  top-level `examples` module can no longer shadow the repository's own.

## [0.6.0] - 2026-09-12

The product is rebuilt around one narrative: an autonomous agent should only
be able to cause consequences that are authorised for the task it performs.
Every surface now answers the same question - does this agent have authority
to cause this consequence, for this task, and where did that authority come
from?

### Added
- **Consequence model** (`agent_plane/consequence`, `config/resources.yaml`):
  every decision derives a structured consequence (effect, environment,
  criticality, customer-facing, reversibility, persistence, downstream
  dependents, blast radius, impact) from an operator-declared resource and
  action catalog. Leases bound it with `permitted_consequence` (max impact,
  environments, customer-facing, reversibility, blast radius) and delegation
  can only narrow it. `CONSEQUENCE_OUTSIDE_TASK_BOUNDARY` denies an in-scope
  action whose effect exceeds the task; the derived consequence also overrides
  the caller-declared `impact`.
- **Decision trace** (`agent-plane.trace.v1`, `GET /v1/decisions/{id}`): identity ->
  task (with origin prompt) -> authority (with full lineage) -> action -> resource
  -> consequence -> decision -> plain-English explanation, recorded on every
  signed audit event and returned in the response (`explanation`, `consequence`).
- **Agent Registry** (`agent_plane/registry`): agents, sessions, and tasks are
  discovered from governed traffic. `GET /v1/agents`, `/v1/agents/{id}` (drift,
  lineage, children, recent decisions), `/v1/tasks`, `POST /v1/tasks` (record an
  intent's origin: prompt/event/human/parent), `/v1/resources`, `/v1/lineage/{id}`,
  `/v1/system`.
- **Authority lineage**: leases carry `parent_lease` and `origin`; delegation
  sets both; decisions explain "No authority lineage permits X" with the chain.
- **Outcomes QUARANTINE and SIMULATE**: `POST /v1/agents/{id}/quarantine` holds an
  agent (HTTP 423); `ENFORCEMENT_MODE=observe` (or `PUT /admin/mode` per tenant)
  returns SIMULATE with `would_be` instead of blocking, feeding
  declared-vs-observed drift and `GET /v1/agents/{id}/suggested-lease` for
  Observe -> Enforce onboarding.
- **Hosted DEMO mode** (`DEMO_ENABLED`, `/demo/*`): three deterministic scenarios
  (staging incident, GitHub maintenance, multi-agent delegation) run through the
  real engine in the isolated `demo` tenant against simulated targets, with
  execution receipts; the console reads the demo tenant with `X-Demo-Token`.
- **New console** (`console/`, Vite + React + Tailwind + Radix, built into
  `agent_plane/console/dist`): LIVE with the Authority–Consequence Graph, Agents
  registry with inspector, Tasks, Resources, Authority × Consequence explorer,
  Decisions with the Decision Inspector, Policies, Timeline, Audit, Integrations,
  Gateway, Runtime, Settings; LIVE/DEMO switch; e-ink instrument visual system.
- **Brand**: vector mark, logo, dark logo, and favicon under `agent_plane/console/brand`.
- SDKs understand `simulate`/`quarantine`, expose `enforced`, `would_be`,
  `consequence`, `explanation`, `proceed`, `register_task`, registry and decision
  reads, and `set_mode`.

### Changed
- `POST /v1/authorize` is now a thin route over `AuthorityService.decide`; the
  MCP gateway uses the same consequence and quarantine checks and feeds the registry.
- The previous operator console, its assets, the `/flow` page, and the browser
  tests are removed in favour of the new console.
- README rewritten around the product narrative; docs gain concepts,
  observe -> enforce, and demo pages.

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
