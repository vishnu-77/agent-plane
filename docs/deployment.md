# Deployment

The operational detail (image, Compose, Helm values, Kubernetes manifests,
metrics, logs, webhooks) is kept next to the artifacts in
[deploy/README.md](../deploy/README.md). This page is the decision guide.

## Topologies

| Topology | Authority + audit state | Replicas | Command |
| --- | --- | --- | --- |
| Single container | SQLite on a named volume | 1 | `docker run … -v agent-plane-data:/data` |
| Compose | Postgres + Redis | any (`--scale gateway=N`) | `docker compose up --build` |
| Kubernetes | shared Postgres (+ Redis) | any | `helm install agent-plane deploy/helm/agent-plane` |

`AUTHORITY_STORE=sql` (default) puts leases, use counters, approvals, and the
MCP request ledger in the same database as the audit chain. With Postgres,
admission takes a transaction-scoped advisory lock and use reservation is a
single atomic update, so replicas never double-spend a use and a revocation
is seen by all of them on the next lookup. SQLite is correct for one host.

## Production checklist

The service refuses to start in `ENVIRONMENT=production` with default
secrets or `AUTHORITY_STORE=memory`. Beyond that, follow
[SECURITY.md](../SECURITY.md): delegation identity, a strong admin token, TLS
and network controls, `/metrics` restricted at the edge, audit shipped to
append-only storage, webhook signatures verified.

## Images

| Dockerfile | Base | Size (0.4, all extras) | Notes |
| --- | --- | --- | --- |
| `Dockerfile` | `python:3.12-slim` | ~287 MB | default; glibc |
| `Dockerfile.alpine` | `python:3.12-alpine` | ~184 MB | musl; all native wheels available |

Both are two-stage builds that ship only a stripped virtualenv, run as UID
10001, and pass `examples/smoke_container.py` (non-root, writable volume,
decisions, audit and lease persistence across a restart). `--build-arg
EXTRAS=server,postgres,redis` drops the MCP, delegation, and token-counting
dependencies when unused.

## Upgrading from 0.3

- Runtime-issued leases now persist. On first start with an existing audit
  database, the YAML leases are seeded once; afterwards the stored copy wins,
  so re-deploying the same YAML never un-revokes a lease.
- `--workers N` is accepted with the SQL store. Prefer replicas over workers
  on Kubernetes.
- The MCP gateway no longer refuses production mode.
- `examples/smoke_container.py` now asserts lease persistence after restart.
