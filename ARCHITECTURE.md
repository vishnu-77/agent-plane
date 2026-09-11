# Architecture

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

## One control plane, many edges

Like a unified API/AI gateway, but built on the core ideas of this project - a
**deterministic** decision, verified identity, and one tamper-evident audit chain,
with **no classifier in the trust path**. The control plane (policy engine +
identity + signed audit) is shared; enforcement is replicated per edge:

| Edge | Endpoint | Status |
| ---- | -------- | ------ |
| Agent → model | `POST /v1/chat/completions` | ✅ |
| Agent → tool / API | `POST /v1/tools/invoke` | ✅ |
| Agent → knowledge (RAG) | `POST /v1/retrieve` | ✅ |
| Agent → agent (A2A) | `POST /v1/agents/delegate` | ✅ |
| Agent → action (task authority) | `POST /v1/authorize` | ✅ |
| Agent → MCP | `POST /mcp` | ✅ (opt-in, `MCP_GATEWAY_FILE`) |
| Human → approval | `/v1/approvals` | ✅ |

The interfaces share identity and audit infrastructure, with different checks.
Task-lease evaluation is explicit on `/v1/authorize` and the configured MCP path;
model, broker, and retrieval routes do not automatically evaluate task leases.
Per-edge walkthroughs and curl examples: [EDGES.md](EDGES.md) and
[docs/integration](docs/integration/README.md).

## Authority state

Leases, per-action use counters, approval requests, and the MCP request-key
ledger live in `SqlLeaseStore` / `SqlApprovalStore`
(`agent_plane/authority/store.py`, `agent_plane/approvals/store.py`), sharing
the audit database. Use reservation is one atomic `UPDATE ... WHERE count <
limit`; on Postgres, admission additionally holds a transaction-scoped
advisory lock, so many replicas can evaluate concurrently without double
spending a use, and a revocation is visible to every replica on its next
lookup. `AUTHORITY_STORE=memory` keeps the earlier single-process behaviour for
tests and is refused in production.

The approval loop is a state machine on top of the same store:
`pending → approved | rejected | expired`, then `approved → consumed` exactly
once when the executor resumes with the approval id. A revoked or expired
lease always wins over a granted approval.

## Postgres + Redis (opt-in)

```bash
docker compose up --build      # sets STORAGE_BACKEND=postgres automatically
```

Identical behavior to the zero-setup SQLite + in-memory path, backed by Postgres
(audit + authority state) and Redis (cache/quota) instead. Helm and plain
Kubernetes manifests are under [deploy/](deploy/README.md).
