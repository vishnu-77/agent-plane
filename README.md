# agent-plane - Enterprise Agentic AI Control Plane

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An open-source (MIT), **deterministic control plane for AI agents**: an
OpenAI-compatible gateway that turns every AI request - *and every tool call* - into a
**governed flow** instead of a raw `prompt → model → response`. Identity, configurable
policy, tool least-privilege, guardrails, routing, **usage metering**, and a
tamper-evident audit log are all applied at runtime. Drop-in (`base_url`),
config-driven, and packaged: `pip install agent-plane` → `agentplane serve`.

> **Capability ≠ Authority.** An agent may hold a real `github.delete_repository`
> credential (its *capability*) - the task at hand might only authorise
> `branch:list`/`branch:delete` on one repo (its *authority*). `POST /v1/authorize`
> decides that, per action, before it reaches the real system - see
> [Task-authority edge](#task-authority-edge-agent--action) and
> [`spec/authority-lease.md`](spec/authority-lease.md).

```
POST /v1/chat/completions
  → Identity (JWT → Actor)
  → Normalize (OpenAI → CanonicalAIRequest; derived data classification + tools)
  → Policy Decision Point (YAML rules → Decision + obligations)
  → Tool least-privilege (enforce Actor.allowed_tools)
  → Token quota
  → Guardrails (PII/secret redaction)
  → Model routing (registry + fallback)
  → Response filtering (redaction + blocked tool calls)
  → Audit logging (hash-chained + HMAC-signed)
```

## Design

- **Control vs data plane.** Policies live as versioned YAML (`policies/*.yaml`);
  the data plane (`agent_plane/gateway`) enforces them. Rules change without redeploying
  apps.
- **Decisions are objects, not booleans.** The PDP (`agent_plane/policy/engine.py`)
  returns a `Decision` carrying route, `max_tokens`, redaction fields,
  obligations, and an audit fingerprint (`decision_id`, `policy_version`,
  `rules_matched`).
- **Swappable backends.** `PolicyDecisionPoint`, `AuditStore`, and `CacheStore`
  are interfaces, so OPA / Postgres / Redis drop in later untouched.
- **Least privilege for tools.** A scoped `Actor.allowed_tools` grant is enforced
  at the decision point (requested tools outside it are denied), and any
  improvised tool call is stripped from the response. Policies can also target
  specific tools (`match.tools`) to require approval.
- **Classification is derived, not trusted.** The caller's `data_classification`
  can only be *escalated* by a content scan, never lowered - labelling a payload
  with secrets/finance content `public` won't bypass policy.
- **Tamper-evident audit.** Each audit event is hash-chained to its predecessor
  and HMAC-signed, so a record can't be silently altered after the fact
  (`agent_plane/audit/signing.py`, `verify_chain`).

## Quick start (zero setup: SQLite + in-memory)

```bash
python -m venv .venv && . .venv/Scripts/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                                # set at least one provider key
agentplane serve --reload
```

**Footprint.** The core install is minimal - the zero-setup path (SQLite + in-memory
+ `jwt_claims`) needs no native-heavy packages. Add only what you use:

| Extra | Pulls in | For |
| ----- | -------- | --- |
| `pip install .` | core | default runtime (SQLite, in-memory, claims auth) |
| `.[delegation]` | cryptography | `IDENTITY_MODE=delegation` (Ed25519) |
| `.[tokens]` | tiktoken | exact token counting (else a char heuristic) |
| `.[postgres]` / `.[redis]` | psycopg / redis | Postgres audit / Redis cache backends |
| `.[server]` | uvicorn[standard] | uvloop/httptools + `--reload` |
| `.[all]` | everything | |

### Mint a dev JWT

```bash
python - <<'PY'
import jwt
print(jwt.encode(
    {"sub": "vishnu", "tenant": "default", "department": "support"},
    "dev-secret-change-me", algorithm="HS256"))
PY
```

### Call the gateway

```bash
TOKEN="<jwt from above>"
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4.1","messages":[{"role":"user","content":"hello"}]}'
```

The response includes an `x_control_plane` block with the `decision_id`,
`policy_version`, `rules_matched`, and any `redactions_applied`.

### Demonstrate governance

```bash
# (a) Confidential finance data to an external model → 403 denied by policy
curl -i http://localhost:8000/v1/chat/completions -H "Authorization: Bearer $FINANCE_TOKEN" \
  -d '{"model":"gpt-4.1","data_classification":"confidential","messages":[{"role":"user","content":"Q3 figures"}]}'

# (b) Same data to the approved private Azure deployment → allowed by the policy exception
curl http://localhost:8000/v1/chat/completions -H "Authorization: Bearer $FINANCE_TOKEN" \
  -d '{"model":"azure-private-gpt4","data_classification":"confidential","messages":[{"role":"user","content":"Q3 figures"}]}'

# (c) Inspect audit evidence
curl http://localhost:8000/v1/audit
```

`$FINANCE_TOKEN` is a dev JWT with `"department": "finance"`.

## Endpoints

| Method | Path                    | Purpose                                  |
| ------ | ----------------------- | ---------------------------------------- |
| POST   | `/v1/chat/completions`  | Governed OpenAI-compatible completion    |
| POST   | `/v1/tools/invoke`      | Governed tool/MCP call (broker edge)     |
| POST   | `/v1/retrieve`          | Identity-aware RAG retrieval (auth edge) |
| POST   | `/v1/agents/delegate`   | Scoped agent-to-agent delegation (A2A)   |
| POST   | `/v1/authorize`         | Task-authority decision (capability ≠ authority) |
| POST   | `/v1/leases`            | Issue an `AuthorityLease` (**admin token only**) |
| GET    | `/v1/leases/{id}`       | Inspect a lease (**admin token only**)   |
| GET    | `/v1/usage`             | Per-tenant usage metering                |
| GET    | `/v1/models`            | Registered logical models                |
| GET    | `/v1/audit?limit=50`    | Recent audit events (**admin token only**) |
| GET    | `/healthz`              | Liveness + active policy bundle version  |
| GET    | `/readyz`               | Readiness (checks the audit store)       |

## View it in the browser

`agentplane serve`, then open:

- **`/console`** - operator dashboard: live status + policy version, all edges/routes, loaded
  policies, per-tenant usage, and the signed audit chain (decisions, who, rules, 🔒). Paste a
  bearer token (for usage) and an `X-Admin-Token` (for audit/policies) into the header fields.
- **`/docs`** - Swagger UI to call every endpoint interactively; **`/redoc`** - reference docs.

The console is a single static page served by the control plane - no build step, no JS deps.

## Policies

Four shipped rules in `policies/`:

- `finance-data-external-model-restriction.yaml` - deny confidential/regulated
  finance & legal data to external models; exception for `azure_openai_private`.
- `pii-redaction-required.yaml` - redact email / credit_card / phone / api_key.
- `token-quota.yaml` - cap `max_tokens`, enforce per-user rolling token quota.
- `sensitive-tool-approval.yaml` - require human approval for high-impact tools
  (`wire_transfer`, `delete_records`, `send_external_email`).

Edit/add YAML and restart; the bundle version changes and is stamped on every
new audit event.

## Identity modes (plug-and-play)

Switch identity verification with one env var - no code change.

- **Dev** (`IDENTITY_MODE=jwt_claims`, default): an HS256 token's claims are trusted,
  including `agent_id` and `allowed_tools`. Simple, but the agent asserts its own scope.
- **Production** (`IDENTITY_MODE=delegation`): the gateway verifies an **Ed25519-signed
  delegation** issued by a trusted authority. The agent can no longer assert its own
  identity or tools - `sub`, `app`, `agent`, and `scope.{tools,clearance}` come from the
  *verified* grant, with expiry and a revocation kill-switch.

```bash
# 1. Generate an issuer keypair
agentplane identity keygen --out-dir keys

# 2. Point the control plane at the public key
export IDENTITY_MODE=delegation
export DELEGATION_PUBLIC_KEY=keys/delegation_public.pem

# 3. Issue a scoped, short-lived agent credential (issuer side)
agentplane identity issue --key keys/delegation_private.pem \
  --sub alice --app crm --agent sales-assistant \
  --tools search,draft_email --clearance internal --ttl 3600
```

The control plane only needs the **public** key; the private key stays with the issuer.
Revoke a credential instantly by adding its `jti` to `REVOKED_JTIS` or `REVOCATION_FILE`.
All knobs live in `.env.example` - drop in your policies (`policies/*.yaml`), set provider
keys, choose backends, and run. That's the plug-and-play surface.

## Models (config-driven)

The model/provider catalog is YAML - onboard models without touching code. Edit
`config/models.yaml` (or point `MODELS_FILE` elsewhere); if neither is present the
built-in defaults in `agent_plane/routing/registry.py` apply. Shipped: `gpt-4.1`,
`gpt-4o-mini` (OpenAI), `claude-sonnet` (Anthropic), `azure-private-gpt4` (Azure),
with `gpt-4.1` falling back to `gpt-4o-mini` on upstream failure.

```yaml
models:
  - id: my-model
    provider: openai
    upstream_model: gpt-4.1
    tags: [openai, external]      # tags are what policies match on
    fallback: [gpt-4o-mini]
```

## Architecture: one control plane, many edges

Like a unified API/AI gateway, but built on the core ideas of this project - a
**deterministic** decision, verified identity, and one tamper-evident audit chain,
with **no classifier in the trust path**. The control plane (policy engine +
identity + signed audit) is shared; enforcement is replicated per edge:

| Edge | Endpoint | Status |
| ---- | -------- | ------ |
| Agent → model | `POST /v1/chat/completions` | ✅ |
| Agent → tool / MCP / API | `POST /v1/tools/invoke` | ✅ |
| Agent → knowledge (RAG) | `POST /v1/retrieve` | ✅ |
| Agent → agent (A2A) | `POST /v1/agents/delegate` | ✅ |
| Agent → data / egress | (egress broker) | planned |

Every edge runs the same flow - *identity → deterministic policy decision →
least-privilege → execute with the broker's credential → one signed audit chain*.

## Tool Broker edge (agent → tool / MCP / API)

Agents don't call sensitive tools directly - they ask the broker, which decides and
executes with **its own** credential (the agent never holds it). Tools are
config-driven (`config/tools.yaml` or `TOOLS_FILE`), **default-deny**.

```bash
curl http://localhost:8000/v1/tools/invoke \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"tool":"search_kb","arguments":{"q":"reset password"}}'
```

The broker reuses the *same* engine and identity as the model edge: `allowed_tools`
least-privilege still applies, `sensitive-tool-approval.yaml` gates high-impact tools
to a human (HTTP 202), unknown tools are denied (404), and the call lands in the same
hash-chained, signed audit log (as `tool:<name>`).

## RAG Authorization edge (agent → knowledge)

**Relevance is not permission.** Retrieval scores candidates, then drops every document
the caller isn't authorized for **before returning** - so the model never sees data the
principal can't access. Sources are config-driven (`config/knowledge.yaml` or
`KNOWLEDGE_FILE`); each document carries access metadata (tenant, departments,
classification, user/group ACL). Authorization is deterministic ABAC/ACL against the
verified `Actor`.

```bash
curl http://localhost:8000/v1/retrieve \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"source":"kb","query":"password reset","top_k":5}'
# x_control_plane shows retrieved vs returned vs filtered_by_authorization
```

A YAML policy with `match: { request_type: retrieval }` can also gate a whole source
(deny/approval). The retrieval lands in the same signed audit chain (`rag:<source>`) and
is metered (`edge=retrieve`).

## Task-authority edge (agent → action)

The other edges decide *is this actor allowed to call this model/tool/source at
all*. This one decides something narrower and newer: **is this specific
action, on this specific resource, authorised for the task the agent is
currently doing** - independent of what it's generically capable of.

An `AuthorityLease` (`config/leases.yaml`, or issued at runtime via
`POST /v1/leases`) grants one agent, for one task, a resource-scoped,
expiring slice of actions - always a subset of its capability manifest
(`Actor.allowed_tools`), never a superset. Full shape and evaluation order in
[`spec/authority-lease.md`](spec/authority-lease.md); runnable end-to-end demo
in [`examples/devops-agent/`](examples/devops-agent/demo.py).

```bash
# devops-agent's lease for `fix-staging-checkout` covers staging/*, not production/*
curl http://localhost:8000/v1/authorize -H "Authorization: Bearer $TOKEN" -d '{
  "task": "fix-staging-checkout", "action": "deployment.restart", "resource": "staging/checkout"
}'
# -> 200 { decision: allow, reason: ACTION_WITHIN_TASK_AUTHORITY, lease: lease-fix-staging, evidence_id: az_... }

curl http://localhost:8000/v1/authorize -H "Authorization: Bearer $TOKEN" -d '{
  "task": "fix-staging-checkout", "action": "deployment.delete", "resource": "production/checkout"
}'
# -> 403 { decision: deny, reason: RESOURCE_OUTSIDE_DELEGATED_SCOPE, lease: null, evidence_id: az_... }
```

Same identity layer, same signed audit chain as every other edge - this adds a
decision point, not a new trust boundary. It's a pure decision, not an
execution broker: the caller (SDK, gateway, or your own code) still calls the
real tool itself once `decision.allowed` is true.

## Agent-to-agent (A2A) edge

A hand-off must not inherit full authority. An authenticated agent asks the control
plane to mint a **child** credential; the plane enforces **attenuation** - the child's
scope must be a subset of the parent's verified scope (escalation → 403) - then issues a
short-lived Ed25519 delegation and audits it. Requires `IDENTITY_MODE=delegation` and a
`DELEGATION_SIGNING_KEY` (the matching private key for `DELEGATION_PUBLIC_KEY`); disabled otherwise.

```bash
curl http://localhost:8000/v1/agents/delegate \
  -H "Authorization: Bearer $PARENT_TOKEN" -H "Content-Type: application/json" \
  -d '{"agent":"child-agent","scope":{"tools":["search"],"clearance":"internal"},"ttl":600}'
# -> { token: <child delegation>, scope, jti, expires_in }   (child ⊆ parent, or 403)
```

The child then calls any edge with its token; its narrower scope is enforced everywhere,
and its `jti` can be revoked instantly via the admin API.

## Admin API (live revocation + policy hot-reload)

Disabled unless `ADMIN_TOKEN` is set; callers pass it in `X-Admin-Token`.

| Method | Path | Purpose |
| ------ | ---- | ------- |
| POST   | `/admin/revocations`        | Revoke a delegation `jti` immediately (kill switch) |
| GET    | `/admin/revocations`        | List runtime revocations |
| DELETE | `/admin/revocations/{jti}`  | Un-revoke |
| GET    | `/admin/policies`           | Active policy bundle version + rule names |
| POST   | `/admin/policies/reload`    | Hot-reload `policies/*.yaml` with no restart |

```bash
curl -X POST localhost:8000/admin/revocations \
  -H "X-Admin-Token: $ADMIN_TOKEN" -d '{"jti":"<delegation-id>"}'
curl -X POST localhost:8000/admin/policies/reload -H "X-Admin-Token: $ADMIN_TOKEN"
```

## Usage metering (bill on usage later)

Every model call and tool call is metered per tenant - the data a billing system would
charge on. Metering is on by default (`USAGE_METERING=true`); pricing is optional.

```bash
curl http://localhost:8000/v1/usage -H "Authorization: Bearer $TOKEN"
# { tenant, items: [{edge, resource, calls, units, estimated_cost}], totals: {...} }
```

`GET /v1/usage?hours=24` scopes to a window. If `config/pricing.yaml` (or `PRICING_FILE`)
is present, an `estimated_cost` is attached - **metering, not billing**; no payment is
taken. This is the hook for usage-based billing later, while staying OSS now.

## Run with Docker

```bash
docker build -t agent-plane .
docker run -p 8000:8000 --env-file .env agent-plane   # runs `agentplane serve`, non-root, healthchecked
```

## Production

Secure-by-default: with `ENVIRONMENT=production` the server **refuses to start**
on default secrets. A fresh install is **governed, not allow-all** - bundled
default policies load when the working dir has none (scaffold your own with
`agentplane init`).

Checklist (see [SECURITY.md](SECURITY.md)):

```bash
ENVIRONMENT=production
JWT_SECRET=<32+ random bytes>
AUDIT_SIGNING_KEY=<32+ random bytes>
IDENTITY_MODE=delegation            # + DELEGATION_PUBLIC_KEY
ADMIN_TOKEN=<strong token>          # or leave admin API disabled
CORS_ORIGINS=https://app.example.com
```

Ops endpoints: `GET /healthz` (liveness + policy version), `GET /readyz`
(readiness - checks the audit store). Every response carries an `X-Request-ID`;
requests are logged with method, path, status, latency, and id.

## Postgres + Redis (opt-in)

```bash
docker compose up --build      # sets STORAGE_BACKEND=postgres automatically
```

Identical behavior, backed by Postgres (audit) and Redis (cache/quota).

## Tests

```bash
pytest                          # policy engine, scanner, full gateway flow (offline)
```

The gateway flow test patches the upstream provider, so it runs without any API
keys.

## Roadmap (not yet built)

Egress/data edge (the planned broker in `docs/architecture/edges.md`), a full MCP
traffic gateway, the human-approval *workflow* (`approval_required` is surfaced as
HTTP 202 but not yet queued/resumed), lease delegation (`child_authority` is parsed
but not enforced - see [`spec/authority-lease.md`](spec/authority-lease.md)), a
TypeScript SDK, a billing integration on top of usage metering, multi-region, SIEM
export, and an admin UI. Full staged plan: [`ROADMAP.md`](ROADMAP.md).

## License

MIT - see [LICENSE](LICENSE). Open source now; contributions welcome.
