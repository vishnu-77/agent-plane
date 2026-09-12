# API reference

Live, interactive reference: `GET /docs` (Swagger UI) and `GET /openapi.json`
on any running instance. The committed export is [openapi.json](openapi.json);
regenerate it with `python examples/export_openapi.py` (CI checks it is
current).

## Authentication

Five credentials reach this API. Only the first two are part of normal use.

| Credential | Presented as | Scope |
| --- | --- | --- |
| Console session | `ap_session` cookie (httponly, signed) | the projects the signed-in user belongs to |
| Project API Key | `Authorization: Bearer ap_live_…` or `X-Api-Key` | one project; scopes `ingest`, `authorize` |
| Management key | `X-Admin-Token: ap_mgmt_…` | one project, read/operate; scope `manage` |
| `ADMIN_TOKEN` | `X-Admin-Token` | every tenant; break-glass. Routes 404 when unset |
| Demo viewer token | `X-Demo-Token` | the demo project only, read-only |

Agent identity tokens (`IDENTITY_MODE=jwt_claims` or `delegation`) are still
accepted on the runtime routes for deployments that mint their own. See
[integration/authorization.md](integration/authorization.md).

Optional request headers on runtime routes, all also settable in the body:
`X-Agent-Id`, `X-Session-Id`, `X-Agent-Host`, `X-Integration`.

---

## Accounts

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/v1/auth/state` | none | `users`, `signup_open`, `first_run`, demo availability |
| POST | `/v1/auth/signup` | none (subject to `SIGNUP_MODE`) | `{email, password, name?, workspace?}`; creates the user and a workspace, sets the session cookie |
| POST | `/v1/auth/login` | none | `{email, password}`; sets the session cookie |
| POST | `/v1/auth/logout` | session | clears the cookie |
| GET | `/v1/auth/me` | session | the user, their workspaces, their projects, and `onboarded` |
| POST | `/v1/auth/exchange` | Project API Key | verify a key, register the integration, learn the project mode and collection policy |
| GET | `/v1/workspaces/{id}/members` | session | members and your role |

## Projects

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/v1/projects` | session | projects with key, integration, and rule counts |
| POST | `/v1/projects` | session | `{name, workspace?, mode?}`; `mode` defaults to `observe` |
| GET | `/v1/projects/{id}` | session | the project plus `collection_fields` |
| PATCH | `/v1/projects/{id}` | session | `{mode?, collection?, name?}` |
| DELETE | `/v1/projects/{id}` | session | delete; the demo project is refused |

## API keys

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/v1/api-keys?project=` | session | masked keys with `status` |
| POST | `/v1/api-keys` | session | `{project, name, environment?, expires_in_days?}`; `secret` is returned once |
| POST | `/v1/api-keys/{id}/rotate` | session | new secret, old key revoked |
| DELETE | `/v1/api-keys/{id}` | session | revoke |

See [accounts.md](accounts.md).

## Integrations

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/v1/integrations?project=` | session / mgmt key / admin / demo | connected integrations and the catalog, each with `observation`, `enforcement`, `enforcement_note` |
| POST | `/v1/integrations` | session | `{project, kind, name?, host?, config?}` |
| DELETE | `/v1/integrations/{id}?project=` | session | forget one |

## Rules

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/v1/rules?project=` | session / mgmt key / admin / demo | rules, the action vocabulary, and the templates |
| POST | `/v1/rules` | session | create |
| PATCH | `/v1/rules/{id}` | session | update |
| DELETE | `/v1/rules/{id}` | session | delete |
| GET | `/v1/rules/suggested?project=&agent=` | session / mgmt key / admin / demo | drafts from observed activity |

See [rules.md](rules.md).

## Reporting activity

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/v1/events/action` | Project API Key (`ingest`) | report one action, or up to 50 under `events` |
| POST | `/v1/sessions` | Project API Key (`ingest`) | announce a session; optional, reporting creates one anyway |
| POST | `/v1/tasks` | Project API Key (`ingest`) or admin | register a task's origin. Provenance, not permission |

```bash
curl -X POST http://127.0.0.1:8000/v1/events/action \
  -H "Authorization: Bearer $AGENTPLANE_API_KEY" -H 'Content-Type: application/json' \
  -d '{"task": "cleanup", "agent": "repo-agent", "integration": "claude-code",
       "tool": "Bash", "arguments": {"command": "git push"},
       "repository": "acme/checkout", "branch": "main"}'
```

Accepted per event: `action` or `tool` (+ `arguments`), `resource` or
`repository`/`branch`, `task`, `agent`, `integration`, `impact`, `approval`,
`context`, `origin`, `session`, `host`.

`/v1/events/action` always answers **200**. The body is the decision:

```json
{"decision": "deny", "reason": "RESOURCE_PROTECTED", "mode": "enforce",
 "enforced": true, "binding": true, "enforcement": "partial",
 "action": "git.push", "resource": "github://acme/checkout/branches/main",
 "task": "cleanup", "agent": "repo-agent",
 "lease": "rules:prj_…:repo-agent:cleanup", "evidence_id": "az_…",
 "consequence": {"impact": "critical", "environment": "source",
                 "reversibility": "reversible", "customer_facing": false,
                 "blast_radius": 3, "summary": ["publishes commits others and CI will build on", "…"]},
 "explanation": ["github://acme/checkout/branches/main is explicitly protected by the task's authority lease; git.push against it is refused regardless of scope. …"]}
```

That is the answer when the project's rules list `git.push` under ASK FIRST
but carve `github://*/branches/main` out as a protected resource. The
`consequence.summary` is a list of structural findings, not prose.

A batch returns `{"results": [ … ]}` in the same order.

## Deciding before an action

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/v1/authorize` | Project API Key (`authorize`) or agent token | decide `{task, action, resource}` |

Optional: `impact` (`reversible` / `irreversible`, checked against the lease
ceiling), `approval` (resume an approved request), `context` (provenance).
`task`, `action`, and `resource` are required.

Unlike `/v1/events/action`, the HTTP status carries the outcome, and non-200
responses wrap the same payload in `detail`:

| Outcome | Status | Body |
| --- | --- | --- |
| `allow`, `simulate` | 200 | top level |
| `approval_required` | 202 | in `detail` |
| `deny` | 403 | in `detail` |
| `quarantine` | 423 | in `detail` |

Do not use `response.ok` or `raise_for_status()` alone to authorize execution.

Every decision payload carries `decision`, `reason`, `lease`, `evidence_id`,
`enforced`, and `mode`, plus `would_be` and `advisory` outside enforce mode,
`approval_id` when one exists, `context` when sent, `consequence`, and
`explanation`.

Approval-related reasons: `ACTION_REQUIRES_APPROVAL`, `APPROVAL_PENDING`,
`ACTION_APPROVED`, `APPROVAL_REJECTED`, `APPROVAL_EXPIRED`,
`APPROVAL_ALREADY_USED`, `APPROVAL_MISMATCH`, `APPROVAL_NOT_FOUND`. Every
other reason code is listed in
[spec/authority-lease.md](../spec/authority-lease.md).

## Activity, agents, and evidence

Readable with a console session (your own project), a management key (its
project), `ADMIN_TOKEN` (every tenant), or the demo token (the demo project).
`?project=` and `?tenant=` name the same thing.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/v1/system?tenant=` | mode, counts, pending approvals, audit head |
| GET | `/v1/agents?tenant=` | discovered agents with declared, granted, exercised, denied authority |
| GET | `/v1/agents/{id}?tenant=` | one agent with leases, lineage, drift, children, recent decisions |
| GET | `/v1/agents/{id}/drift` | declared vs granted vs observed |
| GET | `/v1/agents/{id}/suggested-lease` | a lease inferred from observed behaviour (advanced; prefer `/v1/rules/suggested`) |
| POST / DELETE | `/v1/agents/{id}/quarantine?tenant=` | hold / release an agent (session / mgmt key / admin) |
| GET | `/v1/tasks`, `/v1/tasks/{id}` | tasks with origin, agents, leases, observed actions |
| GET | `/v1/resources` | resources touched and their consequence profiles |
| GET | `/v1/lineage/{lease_id}` | root-first chain of leases and their origins |
| GET | `/v1/decisions?tenant=&limit=` | decision summaries, newest first |
| GET | `/v1/decisions/{id}` | the full `agent-plane.trace.v1` plus the audit event, approval, and receipts |
| GET | `/v1/audit?limit=` | signed audit records (admin) |

## Approvals

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/v1/approvals?status=pending&tenant=&limit=` | session / mgmt key / admin / demo | the queue; `status=all` lists every state |
| GET | `/v1/approvals/{id}` | admin, or the subject agent | one request |
| POST | `/v1/approvals/{id}/approve` | session / mgmt key / admin | `{note?, decided_by?}`; 409 if not pending |
| POST | `/v1/approvals/{id}/reject` | session / mgmt key / admin | same shape |

Approval requests are created only in **enforce** mode. See
[integration/approvals.md](integration/approvals.md).

---

## Advanced surfaces

These are the layer underneath. Nothing in onboarding uses them.

### Leases and delegation

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/v1/leases` | admin | issue a lease from a full document |
| POST | `/v1/leases/from-template` | admin | `{template, subject, task, tenant?, variables, id?}` |
| GET | `/v1/lease-templates` | admin | templates and their variables |
| GET | `/v1/leases/{id}` | admin | read (revoked leases stay readable) |
| PATCH | `/v1/leases/{id}` | admin | shrink only; widening is 403 `privilege_escalation` |
| DELETE | `/v1/leases/{id}` | admin | revoke, effective at the next evaluation everywhere |
| POST | `/v1/leases/{id}/delegate` | the lease holder | mint an attenuated child lease |

### Gateways

Every route in this table authenticates with an **agent identity token**
(`IDENTITY_MODE=jwt_claims` or `delegation`). A Project API Key is not
accepted on these edges.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/v1/chat/completions` | OpenAI-compatible model gateway |
| GET | `/v1/models` | model catalog |
| POST | `/v1/tools/invoke` | broker: `{tool, arguments}` |
| POST | `/v1/retrieve` | `{source, query, top_k}` |
| POST | `/v1/agents/delegate` | scoped child identity token (delegation mode) |
| POST | `/mcp` | MCP Streamable HTTP gateway; mounted only when `MCP_GATEWAY_FILE` is set, so it does not appear in `openapi.json` |
| GET | `/v1/usage` | metered usage for that token's tenant |

### Deployment operations

| Method | Path | Purpose |
| --- | --- | --- |
| GET / PUT | `/admin/mode` | read / set the fallback `observe` or `enforce` for traffic with no project |
| GET / POST / DELETE | `/admin/revocations`, `/admin/revocations/{jti}` | credential revocation |
| GET / POST | `/admin/policies`, `/admin/policies/reload` | policy bundle inspection and hot reload |
| GET | `/admin/leases`, `/admin/gateway` | operator inventory and gateway configuration |

These accept `ADMIN_TOKEN`, a management key, or a console session.

### Demo

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/demo/scenarios` | scenarios and the demo viewer token (when `DEMO_ENABLED`) |
| POST | `/demo/reset` | clear the demo project and simulated targets |
| POST | `/demo/scenarios/{name}/run` | run all steps or `{"steps": [i]}`; returns real decision ids |
| GET | `/demo/targets` | simulated target state |

### Operations

| Path | Purpose |
| --- | --- |
| `/healthz` | liveness |
| `/readyz` | readiness: audit and authority stores reachable |
| `/metrics` | Prometheus text (unauthenticated; `METRICS_ENABLED=false` to disable) |
| `/console` | the console; `/brand/*.svg` the mark |

## Webhook

`APPROVAL_WEBHOOK_URL` receives `agent-plane.approval.v1` events signed with
`X-AgentPlane-Signature: sha256=<HMAC-SHA256(body, AUDIT_SIGNING_KEY)>`. See
[approvals](integration/approvals.md#webhook).
