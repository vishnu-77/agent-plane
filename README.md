# agent-plane

Runtime authorization for AI-agent actions.

Agents often hold tools and credentials with more capability than a specific
task requires. agent-plane evaluates a proposed action against the task at
hand before it reaches the real system.

**Capability ≠ Authority.** An agent may hold a real `github.delete_repository`
credential (its *capability*). The task in front of it might only authorize
`branch.delete` on one repo, on non-`main` branches (its *authority*).
`POST /v1/authorize` decides that, per action, before anything executes.

```text
Agent capability:
  github.delete_repository

Current task ("remediate-stale-branches"), lease-scoped to:
  branch.delete on github://acme/agent-plane-demo/*  (main excluded, 5 uses max)

Decision:
  DENY  github.delete_repository        -> outside the lease entirely
  DENY  branch.delete on .../main       -> protected resource, carved out
  ALLOW branch.delete on .../stale-123  -> within scope
```

```text
request
  -> identity (JWT -> Actor)
  -> policy (YAML rules -> allow / deny / approval_required)
  -> task authority (AuthorityLease: resource + action + limits, per task)
  -> audit (hash-chained, HMAC-signed)
```

## Quickstart

Not yet on PyPI — install from source:

```bash
git clone https://github.com/vishnu-77/agent-plane.git
cd agent-plane && pip install -e .
agentplane serve
```

Runs with zero configuration in dev mode (SQLite + a default JWT secret).
Mint a token and ask whether an action is authorized:

```bash
python -c "import jwt; print(jwt.encode(
    {'sub': 'ops', 'tenant': 'default', 'agent_id': 'devops-agent'},
    'dev-secret-change-me', algorithm='HS256'))"

TOKEN="<paste the token>"

curl -s http://localhost:8000/v1/authorize -H "Authorization: Bearer $TOKEN" -d '{
  "task": "fix-staging-checkout", "action": "deployment.restart", "resource": "staging/checkout"
}'
```

```json
{"decision":"allow","reason":"ACTION_WITHIN_TASK_AUTHORITY","lease":"lease-fix-staging","evidence_id":"az_d14e37168fdb"}
```

Same task, an action/resource outside what the lease actually grants:

```bash
curl -s http://localhost:8000/v1/authorize -H "Authorization: Bearer $TOKEN" -d '{
  "task": "fix-staging-checkout", "action": "deployment.delete", "resource": "production/checkout"
}'
```

```json
// HTTP 403
{"detail":{"decision":"deny","reason":"RESOURCE_OUTSIDE_DELEGATED_SCOPE","lease":null,"evidence_id":"az_2a38d02bd2fe"}}
```

Both examples ran against this repo's own demo data (`config/leases.yaml`)
and its default policies — copy-pasteable as shown.

## Why this exists

A capability grant (an API key, an OAuth scope, a tool the agent can call) is
usually broader than any one task needs, and it doesn't change while the
agent is running. `agent-plane` adds a narrower, expiring, per-task grant
(an `AuthorityLease`) that the agent's raw capability must also satisfy —
before the action reaches the target system, not after.

## What it enforces

- **Identity** — every request carries a JWT resolved to an `Actor` (user,
  agent, tenant, department); unauthenticated requests are rejected, not
  treated as anonymous.
- **Policy** — YAML rules decide allow / deny / approval-required per
  request, with obligations (PII/secret redaction, tool-call filtering).
- **Task authority (leases)** — an `AuthorityLease` binds a task to specific
  resources/actions, with use limits, an expiry, and protected-resource
  carve-outs. A child lease minted via delegation can only narrow a parent's
  grant, never widen it — enforced in code, not just convention.
- **Revocation** — a lease or credential can be revoked immediately; checked
  on every subsequent use, not just at issuance.
- **Audit** — every decision is recorded in a hash-chained, HMAC-signed log.

Full request-authorization pipeline, every edge, and identity modes:
**[EDGES.md](EDGES.md)**. Endpoint table: below.

## Endpoints

| Method | Path                    | Purpose                                  |
| ------ | ----------------------- | ---------------------------------------- |
| POST   | `/v1/authorize`         | Task-authority decision (capability ≠ authority) |
| POST   | `/v1/chat/completions`  | Governed OpenAI-compatible completion    |
| POST   | `/v1/tools/invoke`      | Governed tool/MCP call (broker edge)     |
| POST   | `/v1/retrieve`          | Identity-aware RAG retrieval (auth edge) |
| POST   | `/v1/agents/delegate`   | Scoped agent-to-agent delegation (A2A)   |
| POST   | `/v1/leases`            | Issue an `AuthorityLease` (**admin token only**) |
| POST   | `/v1/leases/{id}/delegate` | Mint an attenuated child lease (lease holder only) |
| PATCH  | `/v1/leases/{id}`       | Narrow an active lease in place (**admin token only**) |
| DELETE | `/v1/leases/{id}`       | Revoke a lease immediately (**admin token only**) |
| GET    | `/v1/usage`             | Per-tenant usage metering                |
| GET    | `/v1/audit?limit=50`    | Recent audit events (**admin token only**) |
| GET    | `/healthz` / `/readyz`  | Liveness / readiness                     |

## View it in the browser

`agentplane serve`, then open `/console` (operator dashboard — status,
policies, usage, audit chain), `/docs` (Swagger UI), or `/redoc`.

## Run with Docker

```bash
docker build -t agent-plane .
docker run -p 8000:8000 --env-file .env agent-plane
```

## Tests

```bash
pytest
```

## Limitations

- Policy and lease decisions are evaluated per-request against configured
  rules — this is authorization, not a guarantee that the underlying model
  or tool behaves correctly.
- PII/secret redaction is regex-based (best-effort), not a guaranteed
  content boundary.
- Audit-chain signing is HMAC (symmetric): tamper-evident against an
  external attacker, not against an insider holding the signing key and
  database write access. Deleting the *trailing* entries of the chain is
  not detectable by chain verification alone.
- `LeaseStore` is in-memory, single-process — leases and usage counters
  don't survive a restart or share across workers/replicas.
- Newest features (lease delegation, revocation, shrinking) are on `main`
  but not yet in a tagged release.

Full production-hardening checklist and known limitations:
**[SECURITY.md](SECURITY.md)**.

## More

- **[EDGES.md](EDGES.md)** — every edge's curl walkthrough, identity modes
- **[CONFIGURATION.md](CONFIGURATION.md)** — policies, models, tools, knowledge, leases, `.env`
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — design principles, control-plane/edge model, Postgres+Redis
- **[INTEGRATION.md](INTEGRATION.md)** — wiring this into an existing product (what's zero-code, what isn't)
- **[spec/authority-lease.md](spec/authority-lease.md)** — the `AuthorityLease` object, evaluation order, reason codes
- **[SECURITY.md](SECURITY.md)** — production hardening checklist, abuse protection, known limitations
- **[ROADMAP.md](ROADMAP.md)** — staged plan, what's shipped vs. planned

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
