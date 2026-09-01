# Edges

Per-edge walkthroughs with curl examples. All of them share one identity
layer, one policy engine, and one signed audit chain - see
[ARCHITECTURE.md](ARCHITECTURE.md) for how they fit together.

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

## Model edge (`POST /v1/chat/completions`)

```bash
# (a) Confidential finance data to an external model → 403 denied by policy
curl -i http://localhost:8000/v1/chat/completions -H "Authorization: Bearer $FINANCE_TOKEN" \
  -d '{"model":"gpt-4.1","data_classification":"confidential","messages":[{"role":"user","content":"Q3 figures"}]}'

# (b) Same data to the approved private Azure deployment → allowed by the policy exception
curl http://localhost:8000/v1/chat/completions -H "Authorization: Bearer $FINANCE_TOKEN" \
  -d '{"model":"azure-private-gpt4","data_classification":"confidential","messages":[{"role":"user","content":"Q3 figures"}]}'
```

`$FINANCE_TOKEN` is a dev JWT with `"department": "finance"`. Governed model catalog:
[CONFIGURATION.md](CONFIGURATION.md).

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

The lease holder can delegate a narrower **child lease** to a sub-agent
(self-service, like the A2A identity edge - child scope must be a subset of
the parent's), and an operator can **shrink or revoke** an active lease
mid-task, effective immediately:

```bash
# devops-agent delegates a read-only slice of its own lease to a sub-agent
curl -X POST http://localhost:8000/v1/leases/lease-fix-staging/delegate \
  -H "Authorization: Bearer $TOKEN" -d '{"agent": "staging-subagent", "actions": ["deployment.read"]}'

# an operator narrows the lease in place - only ever a subset of what it already grants
curl -X PATCH http://localhost:8000/v1/leases/lease-fix-staging -H "X-Admin-Token: $ADMIN" \
  -d '{"actions": ["deployment.read"]}'

# or pulls it outright
curl -X DELETE http://localhost:8000/v1/leases/lease-fix-staging -H "X-Admin-Token: $ADMIN"
```

Same identity layer, same signed audit chain as every other edge - this adds a
decision point, not a new trust boundary. It's a pure decision, not an
execution broker: the caller (SDK, gateway, or your own code) still calls the
real tool itself once `decision.allowed` is true.

CI can enforce that the threat model behind this stays current too:
`agentplane authority check-freshness` fails the build if `config/threat-model.yaml`
drifts from `config/capability-manifest.yaml`'s version.

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
taken.
