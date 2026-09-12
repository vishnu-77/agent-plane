# API reference

Live, interactive reference: `GET /docs` (Swagger UI) and `GET /openapi.json`
on any running instance. The committed export is [openapi.json](openapi.json);
regenerate it with `python examples/export_openapi.py` (CI checks it is
current).

Authentication: `Authorization: Bearer <agent token>` for agent-facing
routes, `X-Admin-Token: <ADMIN_TOKEN>` for operator routes. Admin routes
return 404 when `ADMIN_TOKEN` is unset.

## Task authority

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/v1/authorize` | agent | Decide `{task, action, resource}`; optional `impact` (`reversible`/`irreversible`, checked against the lease ceiling), `approval` (resume), and `context` (provenance). 200 ALLOW top-level; 202 / 403 wrap the decision in `detail`. |
| POST | `/v1/leases` | admin | Issue a lease from a full document (flat or manifest shape). |
| POST | `/v1/leases/from-template` | admin | Issue from `{template, subject, task, tenant?, variables, id?}`; the lease is scoped to `tenant` (default `default`), which must match the agent token's tenant claim. |
| GET | `/v1/lease-templates` | admin | List templates and their variables. |
| GET | `/v1/leases/{id}` | admin | Read a lease (revoked leases stay readable). |
| PATCH | `/v1/leases/{id}` | admin | Shrink: subset of resources/actions/max_uses/expiry/impact; widening is 403 `privilege_escalation`. |
| DELETE | `/v1/leases/{id}` | admin | Revoke, effective on the next evaluation everywhere. |
| POST | `/v1/leases/{id}/delegate` | agent (lease holder) | Mint an attenuated child lease for `{agent, ...narrowing}`. |

Decision payload: `decision` (`allow` / `deny` / `approval_required`),
`reason` (see [spec/authority-lease.md](../spec/authority-lease.md) and the
approval reasons below), `lease`, `evidence_id`, plus `approval_id` and
`context` when present.

Approval-related reasons: `ACTION_REQUIRES_APPROVAL`, `APPROVAL_PENDING`,
`ACTION_APPROVED`, `APPROVAL_REJECTED`, `APPROVAL_EXPIRED`,
`APPROVAL_ALREADY_USED`, `APPROVAL_MISMATCH`, `APPROVAL_NOT_FOUND`.

Every decision response also carries `enforced` (false only for `simulate`),
`would_be` (simulate only), `consequence` (impact, environment,
reversibility, customer_facing, blast_radius, summary), and `explanation`
(plain-English lines). Outcomes: `allow` 200, `simulate` 200, `approval_required`
202, `deny` 403, `quarantine` 423.

## System: registry, lineage, decisions

Readable with the operator token (every tenant) or the demo viewer token
(`X-Demo-Token`, demo tenant only).

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/v1/system?tenant=` | operator/demo | LIVE telemetry: mode, counts, pending approvals, audit head. |
| GET | `/v1/agents?tenant=` | operator/demo | The authority registry: discovered agents with declared, granted, exercised, denied authority. |
| GET | `/v1/agents/{id}?tenant=` | operator/demo | One agent with leases, lineage, drift, children, recent decisions. |
| POST / DELETE | `/v1/agents/{id}/quarantine?tenant=` | admin | Hold / release an agent; held agents get `quarantine` (423). |
| GET | `/v1/agents/{id}/drift` | operator/demo | declared vs granted vs observed. |
| GET | `/v1/agents/{id}/suggested-lease` | operator/demo | A lease inferred from observed behaviour (Observe → Enforce). |
| GET | `/v1/tasks`, `/v1/tasks/{id}` | operator/demo | Tasks with origin, agents, leases, observed actions. |
| POST | `/v1/tasks` | agent or admin | Register an intent: `{task, origin: {kind, ref, text, created_by, parent_task, parent_agent}}`. Provenance, not permission. |
| GET | `/v1/resources` | operator/demo | Resources touched, their consequence profiles, and the catalog. |
| GET | `/v1/lineage/{lease_id}` | operator/demo | Root-first chain of leases and their origins. |
| GET | `/v1/decisions?tenant=&limit=` | operator/demo | Decision summaries newest first. |
| GET | `/v1/decisions/{id}` | operator/demo | The full `agent-plane.trace.v1` plus the audit event, approval, and receipts. |
| GET / PUT | `/admin/mode` | admin | Read / set `observe` or `enforce`, globally or per tenant. |

## Demo

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/demo/scenarios` | Scenarios and the demo viewer token (when `DEMO_ENABLED`). |
| POST | `/demo/reset` | Clear the demo tenant and simulated targets. |
| POST | `/demo/scenarios/{name}/run` | Run all steps or `{"steps": [i]}`; returns real decision ids. |
| GET | `/demo/targets` | Simulated target state. |

## Approvals

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/v1/approvals?status=pending&tenant=&limit=` | admin | Queue. `status=all` lists every state. |
| GET | `/v1/approvals/{id}` | admin or the subject agent | One request. |
| POST | `/v1/approvals/{id}/approve` | admin | Body `{note?, decided_by?}`. 409 if not pending. |
| POST | `/v1/approvals/{id}/reject` | admin | Same shape. |

## Other edges

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/v1/chat/completions` | agent | OpenAI-compatible model gateway. |
| GET | `/v1/models` | agent | Catalog. |
| POST | `/v1/tools/invoke` | agent | Broker: `{tool, arguments}`. |
| POST | `/v1/retrieve` | agent | `{source, query, top_k}`. |
| POST | `/v1/agents/delegate` | agent | Scoped child identity token (delegation mode). |
| POST | `/mcp` | agent | MCP Streamable HTTP gateway (when configured). |
| GET | `/v1/usage` | agent/admin | Metered usage. |
| GET | `/v1/audit?limit=` | admin | Signed audit records, newest first. |
| GET/POST/DELETE | `/admin/revocations`, `/admin/policies`, `/admin/policies/reload` | admin | Credential revocation and policy hot-reload. |

## Operations

| Path | Purpose |
| --- | --- |
| `/healthz` | liveness |
| `/readyz` | readiness: audit and authority stores reachable |
| `/metrics` | Prometheus text (unauthenticated; `METRICS_ENABLED=false` to disable) |
| `/console` | the operator console (LIVE / DEMO); `/brand/*.svg` the mark |

## Webhook

`APPROVAL_WEBHOOK_URL` receives `agent-plane.approval.v1` events signed with
`X-AgentPlane-Signature: sha256=<HMAC-SHA256(body, AUDIT_SIGNING_KEY)>`. See
[approvals](integration/approvals.md#webhook).
