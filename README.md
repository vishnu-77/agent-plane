[![CI](https://github.com/vishnu-77/agent-plane/actions/workflows/ci.yml/badge.svg)](https://github.com/vishnu-77/agent-plane/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

# agent-plane

Runtime least-privilege authorization for AI agents.

Control what an agent is authorized to do for this task — not merely what
its credentials allow it to do.

**Capability ≠ Authority.**

agent-plane sits between an AI agent and the systems it acts on, and
evaluates each proposed action before execution.

## Capability ≠ Authority

```text
Agent capability:
  github.delete_repository

Current task authority ("remediate-stale-branches"):
  branch.delete
  github://acme/agent-plane-demo/*
  main excluded, 5 uses max

DENY  github.delete_repository        -> outside the lease entirely
DENY  branch.delete -> .../main       -> protected resource, carved out
ALLOW branch.delete -> .../stale-123  -> within scope
```

The credential says what the agent *can* do. The task's `AuthorityLease`
says what it's *authorized* to do right now. An action is only allowed
where both agree.

## Architecture

```text
Agent
  |
Identity        JWT -> Actor (user, agent, tenant)
  |
Policy          YAML rules -> allow / deny / approval_required
  |
Task Authority  AuthorityLease -> resource + action + limits, per task
  |
Decision        ALLOW / DENY / APPROVAL_REQUIRED
  |
Execution  ---> Models | MCP / Tools | APIs | RAG / Knowledge | Other Agents
  |
Signed Audit    hash-chained, HMAC-signed
```

Policy governs every edge below. `AuthorityLease` is the task-scoping
primitive that `/v1/authorize` and agent-to-agent delegation evaluate
explicitly — see [§ One authorization plane](#one-authorization-plane-multiple-enforcement-edges).

## Quickstart

Not yet on PyPI — install from source:

```bash
git clone https://github.com/vishnu-77/agent-plane.git
cd agent-plane && pip install -e .
agentplane serve
```

Runs with zero configuration in dev mode (SQLite + a default JWT secret).

```text
Console: http://localhost:8000/console
Swagger: http://localhost:8000/docs
```

## Try it: same identity, different task authority

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

Same agent, same token, same task — a different action/resource the lease
doesn't grant:

```bash
curl -s http://localhost:8000/v1/authorize -H "Authorization: Bearer $TOKEN" -d '{
  "task": "fix-staging-checkout", "action": "deployment.delete", "resource": "production/checkout"
}'
```

```json
// HTTP 403
{"detail":{"decision":"deny","reason":"RESOURCE_OUTSIDE_DELEGATED_SCOPE","lease":null,"evidence_id":"az_2a38d02bd2fe"}}
```

Same identity. Same credentials. Different task authority. Different
decision. Both examples ran against this repo's own demo data
(`config/leases.yaml`) — copy-pasteable as shown.

## Why IAM is not enough

```text
IAM:          What can this identity access?
agent-plane:  What is this agent authorized to do, for this task, right now?
```

```text
Capability ∩ Task Authority ∩ Policy ∩ Runtime Constraints = Executable Authority
```

Cloud and application IAM (AWS IAM, Kubernetes RBAC, OAuth scopes, MCP tool
grants) define the *ceiling* — the most an identity could ever do.
agent-plane adds a narrower, expiring, per-task floor beneath that ceiling,
evaluated at the moment of the action, not once at credential-issue time.
It doesn't replace your IAM — the target system still enforces its own
permissions; agent-plane's decision has to say yes as well.

## AuthorityLease

The primitive that encodes task-scoped authority:

```text
WHO       agent / identity          subject
WHAT      permitted actions         actions
WHERE     permitted resources       resources
WHY       task                      task
HOW LONG  expiry                    expires_at
HOW MUCH  usage limits              max_uses
EXCEPT    protected exclusions      protected_resources
```

Real example, from `config/leases.yaml`:

```yaml
- apiVersion: agent-plane/v1alpha1
  kind: AuthorityLease
  metadata:
    id: lease-clean-branches
    task: remediate-stale-branches
  subject:
    agent: repo-agent
  authority:
    resources: ["github://acme/agent-plane-demo", "github://acme/agent-plane-demo/*"]
    actions: ["repository.read", "branch.list", "branch.delete"]
  constraints:
    protected_resources: ["github://acme/agent-plane-demo/branches/main"]
    max_uses:
      branch.delete: 5
    expires_at: "2027-01-01T00:00:00Z"
```

`repo-agent` may hold the raw `branch.delete` capability broadly, but this
lease narrows it to one repo, `main` excluded, capped at 5 deletes. Full
shape and evaluation order: [spec/authority-lease.md](spec/authority-lease.md).

## Delegation: narrow, never widen

A lease holder can mint a child lease for a sub-agent — self-service, not
admin-gated (`POST /v1/leases/{id}/delegate`). The child's grant is checked
against the parent's on every field; any attempt to exceed it is refused,
not silently capped:

```text
Parent (lease-fix-staging, devops-agent)
staging/*
├── deployment.read
└── deployment.restart

        | delegate

Child (staging-subagent)
staging/*
└── deployment.read
```

Using the same `$TOKEN` from the quickstart above (`devops-agent`, the
lease's actual holder):

```bash
curl -s http://localhost:8000/v1/leases/lease-fix-staging/delegate \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"agent": "staging-subagent", "resources": ["production/*"]}'
```

```json
// HTTP 403 — resources outside parent lease scope
{"detail":{"error":"privilege_escalation","violations":["resources outside parent lease scope: ['production/*']"],"decision_id":"az_4a9fa4d263db"}}
```

Checked on every field: actions, resources, `max_uses`, `maximum_impact`,
`expires_at`, `require_approval` (a child can't quietly drop a safeguard
the parent had), and `protected_resources` (a child can't quietly remove
one). Enforced in code (`lease_attenuation_errors`), not convention —
covered by `tests/test_authority.py`.

## Runtime revocation

```text
issue -> use -> narrow -> revoke
```

Authority can change independently of the underlying long-lived
credential. `DELETE /v1/leases/{id}` revokes a lease immediately; `PATCH
/v1/leases/{id}` narrows one in place (same never-widen rule as
delegation). Both take effect on the *next* evaluation — a lease is
checked live on every `/v1/authorize` call, not cached from issuance.

## One authorization plane. Multiple edges.

```text
Agent -> Action     POST /v1/authorize        pure decision, nothing executes
Human -> Approval   /v1/approvals              approve / reject; executor resumes once
Agent -> MCP tool   POST /mcp                  lease-gated admission, gateway holds the credential
Agent -> Tool       POST /v1/tools/invoke      policy-gated, broker holds the credential
Agent -> Model      POST /v1/chat/completions  policy-gated, OpenAI-compatible
Agent -> Knowledge  POST /v1/retrieve          policy-gated, identity-aware RAG
Agent -> Agent      POST /v1/agents/delegate   scoped identity delegation (A2A)
```

Every edge shares the same identity resolver, the same YAML policy engine,
and the same signed audit chain — not five unrelated products bolted
together. `/v1/authorize` and `/v1/agents/delegate` additionally evaluate
`AuthorityLease`/attenuation, and so does the MCP gateway before it dispatches;
the model, tool, and retrieval edges are governed by policy. Full endpoint
table and curl walkthroughs for every edge: [EDGES.md](EDGES.md) and
[docs/integration](docs/integration/README.md).

## Where a decision actually binds

Two different things are on offer here, and the difference matters more than
any feature in this README.

**Chokepoints — the plane executes, so the answer is binding.** On
`/mcp`, `/v1/tools/invoke`, `/v1/chat/completions` and `/v1/retrieve`, the
credential lives on the server side: the MCP gateway admits a mapped tool call
against the bound lease and dispatches with its own upstream credential, the broker calls the tool with *its* key, the proxy
holds the provider key, retrieval filters before returning. An agent that
skips these edges has no credential to skip them *with* — provided you also
revoke its direct provider/tool credentials, which is on you, not on
agent-plane.

**Decision point — the plane answers, your code obeys.** `/v1/authorize`
executes nothing. It returns allow / deny / approval-required and your caller
does the rest (`if decision.allowed: ...`). An agent that never asks, or
ignores a 403, is not constrained by it. Task authority is a control on an
orchestrator you trust to ask — it is not a sandbox, and it does not contain
a compromised or prompt-injected agent that holds its own credentials.

The MCP gateway closes that gap for agents that speak MCP: point the client at
`/mcp` and every `tools/call` is admitted against the lease before dispatch.
For everything else, the SDK adapters put the check at your tool-dispatch
point in one line, and the conformance kit proves the executor never runs
without ALLOW. Egress interception and per-lease credential minting remain
the open design question, tracked in [ROADMAP.md](ROADMAP.md).

**Human in the loop.** An action inside a lease can still `require_approval`.
The 202 opens a tracked request; an operator approves it in the console, via
`/v1/approvals`, or from a signed webhook receiver; the executor resumes with
the approval id and gets ALLOW exactly once. A revoked lease beats a granted
approval. See [docs/integration/approvals.md](docs/integration/approvals.md).

### What the decision itself covers

**Identity** — JWT resolves to an `Actor`; unauthenticated requests are
rejected, not treated as anonymous.

**Policy** — deterministic YAML rules decide allow / deny /
approval-required, with obligations (PII/secret redaction, tool-call
filtering).

**Task authority** — `AuthorityLease` restricts actions and resources per
task, with use limits and an expiry.

**Delegation** — child authority can only be attenuated, never widened.

**Revocation** — authority can be revoked or narrowed at runtime, checked
on every use.

**Protected resources** — specific resources are carved out even within a
broader granted scope.

**Audit** — every decision produces a hash-chained, HMAC-signed record.

Known limits on all of the above: [SECURITY.md](SECURITY.md).

## Deployment model

```text
Agent runtime (LangGraph / custom agent / managed agent)
        |
   agent-plane
        |
MCP / GitHub / cloud APIs / internal APIs / databases
```

agent-plane is a service your agent calls before (or through) acting — a
`base_url` swap for the OpenAI-compatible edge, a broker call for tools, a
decision call for the task-authority edge, or an MCP endpoint swap. It is not
a code-free proxy for every managed agent platform; the
[integration guides](docs/integration/README.md) say exactly what is
zero-code (model calls, MCP) and what is one wrapper at your dispatch point
(everything else, with adapters for LangChain, CrewAI, OpenAI Agents, and
custom loops in the [Python](sdk/python/README.md) and
[TypeScript](sdk/typescript/README.md) SDKs).

Leases, use counters, approvals, and the MCP request ledger live in the same
database as the audit chain (SQLite by default, Postgres for more than one
replica), so a revocation reaches every worker and a restart forgets nothing.
Helm chart and Kubernetes manifests: [deploy/](deploy/README.md).

## Run with Docker

```bash
docker build -t agent-plane .                       # ~287 MB; Dockerfile.alpine ~184 MB
docker run -p 127.0.0.1:8000:8000 --env-file .env \
  -e SQLITE_PATH=/data/audit.db -v agent-plane-data:/data agent-plane
```

The volume keeps audit records, leases, use counters, and approvals across
restarts. `docker compose up --build` runs the Postgres + Redis profile.

## Security-sensitive behaviour covered by tests

Run `pytest` for the full suite. Not exhaustive — these are the security-relevant ones:

```text
✓ parent -> child authority attenuation refused on any widened field
✓ revoked lease rejected on next use
✓ lease expiry enforced
✓ protected-resource exclusion denied even within broader scope
✓ usage-limit (max_uses) enforcement
✓ unauthenticated request rejected (401)
✓ audit-chain tamper detection (in-place edit, deleted middle entry)
✓ malformed policy file fails startup closed, doesn't corrupt a live reload
✓ empty policy bundle refuses to start in production (not silent allow-all)
✓ tenant isolation — same agent_id/task string can't cross tenants
```

## Security model / what agent-plane is not

agent-plane is **not**:
- an LLM safety classifier or prompt-injection detector
- a replacement for AWS IAM, Azure RBAC, or Kubernetes RBAC
- a guarantee that the underlying model behaves correctly
- a guarantee that an external tool executes safely

agent-plane constrains the authority under which agent actions execute.
The target system's own permissions still apply underneath it.

## Current limitations

Four that decide whether this fits your deployment at all:

- **`/v1/authorize` decides, it does not execute** — an agent that never asks
  is not constrained by it.
- **`impact` is caller-declared**, and defaults to the permissive value.
- **Runtime credential revocations (`/admin/revocations`) live in process
  memory**; leases, use counters, and approvals are durable and shared, but
  that revocation set is not — use `REVOKED_JTIS` / `REVOCATION_FILE` for
  revocations that must survive a restart.
- **Audit truncation is undetectable** and signing is symmetric (HMAC).

The full list, with what each one does and doesn't cover, is maintained in one
place: **[SECURITY.md § Known limitations](SECURITY.md#known-limitations-read-before-relying-on-it)**.
That file also carries the production-hardening checklist.

## Repository guide

- **[docs/](docs/README.md)** — quickstart, per-edge integration guides, approvals, adapters, conformance kit, API reference, deployment
- **[EDGES.md](EDGES.md)** — every edge's curl walkthrough, identity modes
- **[CONFIGURATION.md](CONFIGURATION.md)** — policies, models, tools, knowledge, leases, `.env`
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — design principles, control-plane/edge model, Postgres+Redis
- **[INTEGRATION.md](INTEGRATION.md)** — pointer to the integration guides (what's zero-code, what isn't)
- **[deploy/](deploy/README.md)** — container, Compose, Helm chart, Kubernetes manifests
- **[sdk/python](sdk/python/README.md)** / **[sdk/typescript](sdk/typescript/README.md)** — clients, adapters, conformance kit
- **[spec/authority-lease.md](spec/authority-lease.md)** — the `AuthorityLease` object, evaluation order, reason codes
- **[SECURITY.md](SECURITY.md)** — production hardening checklist, abuse protection, known limitations
- **[ROADMAP.md](ROADMAP.md)** — staged plan, what's shipped vs. planned

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
