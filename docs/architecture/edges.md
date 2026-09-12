# One Control Plane, Many Edges

*Architecture note, not an adoption path.* Nothing here is what a developer
starts with: onboarding is [quickstart](../quickstart.md) ->
[connectors](../connectors.md) -> [rules](../rules.md) -> [modes](../modes.md).
This page describes the enforcement edges the runtime can sit on and the
design position behind them.

*Target architecture: a unified API/AI mesh (à la Kong's "Unified API and AI Platform")
- built on this project's core ideas, not a vendor's.*

## The shape

A broker sits on every edge an agent acts across. But all brokers share **one control
plane** - they are enforcement points (data plane), not independent products.

```
                         ┌──────────────  CONTROL PLANE  ──────────────┐
                         │  Policy engine (deterministic PDP)          │
                         │  Identity (verified Ed25519 delegation)     │
                         │  Audit (one hash-chained, signed log)       │
                         └──────────────────────────────────────────────┘
                              ▲          ▲           ▲          ▲
        human/agent ──┐       │          │           │          │
                      ▼   [edge: model] [edge: tool] [edge: data] [edge: A2A]
                  AI APP/AGENT  ─────────────────────────────────────────►  LLMs / MCP / APIs / DB / data / agents
```

Each edge runs the identical flow:
**identity → deterministic policy decision → least-privilege → execute with the
broker's credential → record in the one signed audit chain.**

## Edges

| Edge | Endpoint | What it governs | Status |
| ---- | -------- | --------------- | ------ |
| Agent → model | `POST /v1/chat/completions` | model request, classification, redaction, routing, quota | ✅ implemented |
| Agent → reported activity | `POST /v1/events/action` | the ordinary path: canonical action + resource, project rules, consequence, one signed decision | ✅ implemented |
| Agent → tool / MCP / API | `POST /v1/tools/invoke`, `POST /mcp` | tool authorization (per-tool + `allowed_tools`), approval, default-deny, execution with broker credential | ✅ implemented |
| Agent → knowledge (RAG) | `POST /v1/retrieve` | identity-aware retrieval: per-document ABAC/ACL filter (tenant, dept, clearance, group) applied *before* generation - relevance is not permission | ✅ implemented |
| Agent → agent (A2A) | `POST /v1/agents/delegate` | scoped delegation: mint an attenuated child credential (child scope ⊆ parent scope - no privilege escalation) | ✅ implemented |
| Agent → data / egress | egress broker | which data may *leave*, to which sink | planned |

## Our core ideas (vs. the unified-gateway vendors)

What makes this *ours*, not a reskin of a vendor mesh:

1. **Deterministic decision, no classifier in the trust path.** The allow/deny is a
   contract from a policy engine, not a probabilistic guard model. (Vendors lean on
   classifier-based detection; we keep that strictly on observability.)
2. **Decision-as-object.** One object carries route, obligations, redactions, and the
   audit fingerprint - authz + DLP + routing + provenance unified.
3. **Verified identity, not asserted.** `IDENTITY_MODE=delegation` verifies an
   Ed25519-signed grant; agents can't self-assert scope. Live revocation via the admin API.
4. **One tamper-evident audit chain across every edge.** Model calls and tool calls
   hash-chain into the same signed log - a single replayable record of everything the
   agent did, provable to a third party.
5. **Authored by developers, not by config.** Authority is written as rules -
   allow / ask first / never - in the console or over `/v1/rules`, and compiled
   into the leases the engine evaluates. The edge-level configuration (models
   `config/models.yaml`, tools `config/tools.yaml`, policies `policies/*.yaml`,
   identity mode, backends) is the operator's layer underneath.

## Honest gaps (the planned edges)

- **Egress / data edge** - governing what data *leaves* to which sink (the right side of the
  diagram: DB, lakehouse, data, events) needs an egress broker. (Knowledge *retrieval* is now
  governed by the RAG edge above; egress is the complementary out-bound direction.)
- **Agent-to-agent** - scoped delegation between agents (so a hand-off doesn't inherit full
  authority) is designed (verified delegation supports it) but not yet a dedicated edge.
- **Deep MCP mediation** - we broker tool *names + arguments*; per-call inspection of live MCP
  servers (tool-poisoning, rug-pulls) is a further increment.
