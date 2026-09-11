# Deploying agent-plane

Three paths, one image. Pick by where the rest of your platform runs.

| Path | Use when | State |
| --- | --- | --- |
| `docker run` | one host, pilot, CI | SQLite on a named volume (`/data`) |
| `docker compose up` | one host with Postgres + Redis | Postgres audit + authority, Redis cache/quota |
| Helm / Kubernetes | production, more than one replica | shared Postgres (required), Redis |

Since 0.4 the authority state (leases, use counters, approvals, gateway
request ledger) lives in the same database as the audit chain. That is what
lets a revocation reach every replica and lets a lease survive a restart.
`AUTHORITY_STORE=memory` restores the old process-local behaviour for tests
and is refused in `ENVIRONMENT=production`.

## Container image

```bash
docker build -t agent-plane .
docker run -p 127.0.0.1:8000:8000 --env-file .env \
  -e SQLITE_PATH=/data/audit.db -v agent-plane-data:/data agent-plane
```

The Dockerfile is a two-stage build: the wheel is built and installed with
all extras into a virtualenv, then only that virtualenv is copied onto a
`python:3.12-slim` base with pip, setuptools, bytecode caches, bundled tests,
and unstripped native symbols removed. Trim further with the `EXTRAS` build
argument, for example `--build-arg EXTRAS=server,postgres,redis` when the MCP
gateway, delegation identity, and exact token counting are not needed.

The image runs as UID 10001 with a read-only root filesystem friendly layout:
only `/data` (SQLite) and `/tmp` need to be writable.

## Helm

```bash
helm install agent-plane deploy/helm/agent-plane \
  --namespace agent-plane --create-namespace \
  --set existingSecret=agent-plane-secrets \
  --set postgres.url=postgresql+psycopg://user:pass@postgres:5432/agentplane
```

Create the secret first with at least `JWT_SECRET`, `ADMIN_TOKEN`, and
`AUDIT_SIGNING_KEY` (32+ random bytes each), plus `POSTGRES_URL` if not set
through values. `values.yaml` documents every knob: policies and config YAML
mounted from ConfigMaps, ingress, HPA, PodDisruptionBudget, Prometheus
scrape annotations, and the MCP gateway file.

`kubectl apply -f deploy/kubernetes/agent-plane.yaml` is the plain-manifest
equivalent for clusters without Helm.

## Observability

- `GET /healthz` liveness, `GET /readyz` readiness (checks the audit and
  authority stores).
- `GET /metrics` Prometheus text: request counts and latency by route,
  decisions by edge/decision/reason. Unauthenticated; block it at the ingress
  if the service is public, or set `METRICS_ENABLED=false`.
- `LOG_FORMAT=json` for one-JSON-object-per-line logs with request ids.

## Approval webhooks

Set `APPROVAL_WEBHOOK_URL` to receive signed `approval.requested`,
`approval.approved`, and `approval.rejected` events. Verify
`X-AgentPlane-Signature` (HMAC-SHA256 with `AUDIT_SIGNING_KEY`) before acting;
`agent_plane.approvals.notify.verify_signature` is a reference implementation.
