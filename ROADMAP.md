# Roadmap

Ship one narrow, technically credible decision primitive, then expand the
plane around it - not the whole architecture at once.

**Milestones are not package versions.** The rows below are capability
milestones (M1, M2, ...); the shipped package version is in `pyproject.toml`
and `CHANGELOG.md`. Milestones M1-M5 are present in package 0.5.0.

| Milestone | Primary capability                      | Status |
| -------- | ---------------------------------------- | ------ |
| **M1** | Runtime task-authority decisions (`AuthorityLease`, `POST /v1/authorize`, capability-manifest gate, tamper-evident evidence) | ✅ shipped |
| **M2** | TypeScript SDK, MCP adapter, gateway/proxy mode | ✅ shipped in 0.5.0: `/mcp` gateway with lease-gated admission and upstream credential separation, `@agent-plane/sdk`, Python adapters for LangChain / CrewAI / OpenAI Agents / custom loops, approval loop, durable shared authority store |
| **M3** | Lease delegation + child authority (attenuated sub-leases, mirroring the A2A identity edge) | ✅ shipped |
| **M4** | Dynamic authority shrinking/revocation for active leases | ✅ shipped |
| **M5** | Consequence-aware decisions (`maximum_impact` actually gates, not just informational) | ✅ shipped, and no longer just caller-declared: `config/resources.yaml`'s `actions:` block is a real per-action impact registry (`severity`, independently classified), used by `agent_plane/consequence/catalog.py` to compute `Consequence.impact` from the resource actually named, not only what the caller declares. |
| **M6** | Observed-vs-declared capability drift detection | 🟡 started: `agentplane authority check-freshness` fails CI when `config/threat-model.yaml` drifts from `config/capability-manifest.yaml`'s version. Declared-vs-declared only so far, not declared-vs-*observed* runtime behavior. |
| **M7** | Reconciliation / rollback hooks | planned |
| **M8** | Full authority lifecycle + attestations | planned |
| **M9** | Typed consequence envelopes | ✅ shipped: `ConsequenceEnvelope` (`agent_plane/consequence/envelope.py`) replaced three separate, inconsistent copies of the same bound-checking logic - including a rule-compilation bug where the first applicable rule's `max_impact`/`max_reversibility` silently won over every later rule's. |
| **M10** | Causal consequence reachability | ✅ shipped: `Consequence.impact`/`customer_facing`/`reversibility` now reflect the worst *reachable* resource, not just the one an action targets; a lease's envelope is checked on sensitive reads too, not only mutations; optional typed `transitions:` (`agent_plane/consequence/graph.py`) trace action-conditioned causal paths - not just "is this reachable" but "why, and through what" - bounded by `max_depth`/`allowed_terminal_resources`/`forbidden_terminal_resources`. |
| **M11** | Task-state consequence composition | ✅ shipped: `agent_plane/consequence/state.py` accumulates per-task facts (`proposed`/`confirmed`, CAS'd by an explicit revision), classified by an operator-declared `semantic_class` on each resource. A `transitions:` edge can require a task fact before it's traversable, so `decide()` asks "what's reachable *given what this task has already done*," not just "what does this one action do alone." |
| **M12** | Confirmed runtime consequence enforcement | ✅ shipped for Claude Code, unverified in practice: `POST /v1/events/action {"confirms": ...}` flips a proposed fact to confirmed - what `agentplane connect`'s new PostToolUse hook sends once a tool actually ran, correlated back to its PreToolUse decision by a small local cache. Only confirmed facts gate a *binding* Enforce-mode denial. The PostToolUse payload shape this assumes could not be checked live while building it; if it doesn't fire as expected, decisions degrade to proposed-only (Observe/Govern still work; Enforce simply never binds on state it never confirmed) rather than failing. Codex is untouched pending the same verification. |
| **M13** | Consequence benchmark + evaluation harness | planned: scenarios where every individual action is legitimate and only the composition overreaches, scored against prompt-only, context-engineered, and stateless-harness baselines. |

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

Durability is solved as of 0.5.0: leases, use counters, approvals, and the MCP
request ledger live in the shared SQL authority store, so path 1 is now real
for MCP-speaking agents. Runtime *credential* revocations (`/admin/revocations`)
are still per process; path 2 (per-lease credential minting) is open.

v0.1 lives in `agent_plane/authority/` + `POST /v1/authorize` /
`POST /v1/leases`; see [`spec/authority-lease.md`](spec/authority-lease.md) for
the object shape and evaluation order, and
[`examples/devops-agent/demo.py`](examples/devops-agent/demo.py) for the
capability-vs-authority demo end to end.
