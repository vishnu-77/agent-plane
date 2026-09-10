# Roadmap

Ship one narrow, technically credible decision primitive, then expand the
plane around it - not the whole architecture at once.

**Milestones are not package versions.** The rows below are capability
milestones (M1, M2, ...); the shipped package version is in `pyproject.toml`
and `CHANGELOG.md`. Milestones M1-M5 are all present in package 0.4.0.

| Milestone | Primary capability                      | Status |
| -------- | ---------------------------------------- | ------ |
| **M1** | Runtime task-authority decisions (`AuthorityLease`, `POST /v1/authorize`, capability-manifest gate, tamper-evident evidence) | ✅ shipped |
| **M2** | TypeScript SDK, MCP adapter, gateway/proxy mode | planned |
| **M3** | Lease delegation + child authority (attenuated sub-leases, mirroring the A2A identity edge) | ✅ shipped |
| **M4** | Dynamic authority shrinking/revocation for active leases | ✅ shipped |
| **M5** | Consequence-aware decisions (`maximum_impact` actually gates, not just informational) | ✅ shipped: `POST /v1/authorize` takes a caller-declared `impact`, denied outright if it outranks the matched lease's ceiling. Caller-declared, not independently classified - no per-action impact registry yet. |
| **M6** | Observed-vs-declared capability drift detection | 🟡 started: `agentplane authority check-freshness` fails CI when `config/threat-model.yaml` drifts from `config/capability-manifest.yaml`'s version. Declared-vs-declared only so far, not declared-vs-*observed* runtime behavior. |
| **M7** | Reconciliation / rollback hooks | planned |
| **M8** | Full authority lifecycle + attestations | planned |

## The open design question: making a decision binding

`/v1/authorize` returns a decision and executes nothing, so it governs an
orchestrator that chooses to ask. Closing that is the project's central
unsolved problem, not a backlog item. Three candidate paths:

1. **Route execution through the broker** (extends M2's gateway/proxy mode).
   The tool broker and model proxy already enforce because they hold the
   credential; widening that to every action makes the plane a real chokepoint,
   at the cost of proxying everything.
2. **Per-lease credential minting** — issue short-lived credentials scoped to
   the lease, so enforcement lands on the resource server. Depends on
   downstream systems honouring it.
3. **Stay a decision point** and position accordingly, leaning on the audit
   chain as the product.

Durability (leases/revocations in memory) has to be solved before 1 or 2 is
meaningful — a chokepoint that forgets its revocations on restart is not one.

v0.1 lives in `agent_plane/authority/` + `POST /v1/authorize` /
`POST /v1/leases`; see [`spec/authority-lease.md`](spec/authority-lease.md) for
the object shape and evaluation order, and
[`examples/devops-agent/demo.py`](examples/devops-agent/demo.py) for the
capability-vs-authority demo end to end.
