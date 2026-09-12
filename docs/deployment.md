# Deployment

Self-hosting agent-plane for a team. The operational detail (image, Compose,
Helm values, Kubernetes manifests, metrics, logs, webhooks) is kept next to
the artifacts in [deploy/README.md](../deploy/README.md). This page is the
decision guide.

A single developer does not need any of this: `agentplane serve` on a laptop
stores everything in `audit.db` and needs no configuration.

## Topologies

| Topology | State | Replicas | Command |
| --- | --- | --- | --- |
| Single container | SQLite on a named volume | 1 | `docker run … -v agent-plane-data:/data` |
| Compose | Postgres + Redis | any (`--scale gateway=N`) | `docker compose up --build` |
| Kubernetes | shared Postgres (+ Redis) | any | `helm install agent-plane deploy/helm/agent-plane` |

`AUTHORITY_STORE=sql` (default) puts leases, use counters, approvals, and the
MCP request ledger in the same database as the audit chain. Accounts,
projects, API keys, and rules live there too. With Postgres, admission takes a
transaction-scoped advisory lock and use reservation is a single atomic
update, so replicas never double-spend a use and a revocation is seen by all
of them on the next lookup. SQLite is correct for one host.

## Configuration that matters for a team instance

| Setting | Why |
| --- | --- |
| `API_KEY_SECRET_VALUE` | derives the stored HMAC of every API key and signs session cookies. Defaults to `JWT_SECRET`; set it explicitly. Rotating it invalidates every key and signs everyone out. |
| `SIGNUP_MODE` | `first_user` (default) closes sign-up after the first account; `open` allows anyone; `closed` allows nobody. |
| `SESSION_TTL_SECONDS` | console session lifetime; 14 days by default. |
| `SECURE_COOKIES` | send the session cookie only over HTTPS. Forced on in production. |
| `CORS_ORIGINS` | only if the console is served from another origin. |
| `ENFORCEMENT_MODE` | the fallback for traffic that belongs to no project. A project's own mode always wins. |
| `DEMO_ENABLED` | turn the demo project off on anything that is not the hosted demo. |
| `ADMIN_TOKEN` | optional. Enables the deployment-wide break-glass routes; they return 404 when it is unset. |
| `AUDIT_SIGNING_KEY` | signs the audit chain and the approval webhook. |

The full list is in [CONFIGURATION.md](../CONFIGURATION.md) and
[.env.example](../.env.example).

## Production checklist

The service refuses to start in `ENVIRONMENT=production` with default secrets
or `AUTHORITY_STORE=memory`. Beyond that, follow
[SECURITY.md](../SECURITY.md): TLS and network controls, `/metrics` restricted
at the edge, audit shipped to append-only storage, webhook signatures
verified, and signed delegation identity if you mint agent identity tokens.

Note that `ADMIN_TOKEN`, a management key, and **any signed-in console
session** all satisfy the deployment-level operations under `/admin/*`
(policy reload, credential revocation, the global mode fallback). Treat
console accounts on a shared instance accordingly.

## Images

| Dockerfile | Base | Notes |
| --- | --- | --- |
| `Dockerfile` | `python:3.12-slim` | default; glibc |
| `Dockerfile.alpine` | `python:3.12-alpine` | musl; all native wheels available |

Both are two-stage builds that ship only a stripped virtualenv, run as UID
10001, and pass `examples/smoke_container.py` (non-root, writable volume,
decisions, audit and lease persistence across a restart).
`--build-arg EXTRAS=server,postgres,redis` drops the MCP, delegation, and
token-counting dependencies when unused.

## Upgrade notes

- Accounts, projects, API keys, and rules are new tables in the same database.
  They are created on start; nothing has to be migrated by hand.
- An existing deployment keeps enforcing: `ENFORCEMENT_MODE` still applies to
  traffic that belongs to no project. New projects created through onboarding
  start in `observe` regardless of that value.
- Runtime-issued leases persist. On first start with an existing audit
  database the YAML leases are seeded once; afterwards the stored copy wins,
  so re-deploying the same YAML never un-revokes a lease.
- `--workers N` is accepted with the SQL store. Prefer replicas over workers
  on Kubernetes.
