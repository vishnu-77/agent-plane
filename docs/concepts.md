# Concepts

agent-plane answers one question better than anything else:

> Does this agent have authority to cause this consequence, for this task,
> and where did that authority come from?

This page has two halves. The first is everything you need to adopt it. The
second is the machinery underneath, which you can read when you need it.

---

# Part one: the product

## The objects you work with

```text
User -- Workspace -- Project -- API key -- Connector -- Activity
                        |
                        +-- Rules    allow / ask first / never
                        +-- Mode     observe / govern / enforce
```

| Object | What it is |
| --- | --- |
| **Project** | one governed environment. Everything below belongs to exactly one. |
| **API key** | `ap_live_` / `ap_test_` / `ap_mgmt_`; hashed at rest, shown once. |
| **Connector** | what reports on an agent's behalf: a coding-agent hook, the MCP gateway, the SDK. |
| **Rule** | ALLOW / ASK FIRST / NEVER over canonical actions, scoped to agents, integrations, environments. |
| **Mode** | how binding a decision is: observe, govern, enforce. |
| **Activity** | the recorded decisions - what an agent did, what it would affect, what decided it, why. |

Details: [accounts.md](accounts.md) - [connectors.md](connectors.md) -
[rules.md](rules.md) - [modes.md](modes.md).

## The loop

```text
CONNECT     an agent reports what it is about to do
OBSERVE     nothing is blocked; every attempt is recorded
UNDERSTAND  suggested rules, drift, and the consequence of each action
GOVERN      the real decision comes back, still not binding
ENFORCE     the decision binds, where the connector can bind it
```

Adoption runs in that order because nobody can write a complete authority
model for agents they have not watched.

## What a decision is made of

An action is reported. It is normalized into a canonical action and a
canonical resource - from the tool name and the shape of its arguments, never
from prompt text or file contents. Then:

```text
IDENTITY      which project, which agent, which session
TASK          the unit of work authority is granted for
AUTHORITY     the rules that apply, compiled into a lease
ACTION        the canonical action
RESOURCE      the canonical resource
CONSEQUENCE   what allowing it would cause
DECISION      allow / deny / approval_required / quarantine / simulate
EVIDENCE      a signed, hash-chained record of all of the above
```

Outcomes:

| Outcome | HTTP on `/v1/authorize` | Meaning |
| --- | --- | --- |
| `allow` | 200 | execute exactly this action |
| `deny` | 403 | do not execute; the reason and explanation say why |
| `approval_required` | 202 | a human must approve; in enforce mode, resume once with the approval id |
| `quarantine` | 423 | an operator is holding this agent; nothing proceeds, in any mode |
| `simulate` | 200 | observe mode: recorded, not enforced; `would_be` says what enforce would return |

`POST /v1/events/action` always answers 200 and puts the same decision in the
body, because a connector reporting activity is not itself being refused
service.

## Capability is not authority

A credential is a ceiling: what an agent *can* technically do. Authority is
what it is *authorised* to do now, for this task. agent-plane never touches
the credential. It sits between the agent and the action.

That also sets the honest limit: if an agent holds the target credential and
a network route to the target, the check is advisory however the project is
configured. What a connector can and cannot stop is declared per integration
and returned on every decision.

## Authority without consequence is incomplete

`deployment.restart` is one string. Against a development sandbox, a staging
workload, and a production payment service it produces three different
effects. A decision therefore considers the **consequence**: what state
changes, what depends on it downstream, in which environment, how reversible,
how persistent, how many customers notice.

The consequence is derived from an operator-declared catalog
(`config/resources.yaml`) and stays structural. There is no risk score. A rule
bounds each dimension separately through `permitted_consequence`.

---

# Part two: underneath

You do not need this to use agent-plane. You need it to extend it.

## AuthorityLease

A rule is not what the engine evaluates. A rule is compiled into an
**AuthorityLease**: one agent, one task, a set of actions, a set of resources,
protected carve-outs, use limits, an expiry, an impact ceiling, and a
permitted consequence. Leases can also be issued directly through the admin
API, and both kinds are evaluated together.

See [rules.md](rules.md#how-rules-become-decisions) for the compilation, and
[spec/authority-lease.md](../spec/authority-lease.md) for the object and every
reason code.

## Agent registry: who exists?

Agents are discovered, not registered by hand. Every governed call upserts the
acting agent, its session, and its task. Leases attach granted authority,
delegations attach lineage, decisions attach what was exercised, denied, and
touched.

```text
Project
  Integration
    Agent
      Session
        Task
          AuthorityLease
```

`GET /v1/agents/{id}` answers: who is this, why is it running, what started
it, what could it do (declared capabilities), what may it do (granted), where
that came from (lineage), what it has exercised, and how far it has drifted
from what was expected.

## Authority lineage: why can this agent do this?

Authority flows from an origin (a human, an event, a schedule, a parent agent)
into a lease, and from a lease into narrower child leases.

```text
Human --"Investigate checkout. Do not modify production."--> incident-agent
                                                               | logs.read metrics.read deployment.read
                                                               +--> metrics-agent
                                                                      metrics.read
```

The invariant is that child authority is a subset of parent authority:
delegation can only attenuate actions, resources, use limits, expiry, impact,
and permitted consequence. When `metrics-agent` asks for
`deployment.restart`, the answer is not "policy says no"; it is "no authority
lineage permits it", with the chain.

Prompts are provenance, not permission. A prompt can be recorded as the origin
of a task (`POST /v1/tasks`) so every later decision traces back to it, but an
LLM cannot grant itself anything.

## The decision model

```text
Executable Authority =
  Identity
  and Task Authority          compiled rules and issued leases
  and Delegated Authority     lineage never widens
  and Resource Scope          lease resources, minus protected resources
  and not Never               absolute refusal, checked first
  and Runtime Constraints     expiry, use limits, approvals, revocation, quarantine
  and Permitted Consequence   max impact, environments, customer-facing,
                              reversibility, blast radius
```

The model, tool, and retrieval gateways additionally apply organisation policy
files (`policies/*.yaml`) and the identity's capability manifest. Those edges
are advanced surfaces; see [integration/README.md](integration/README.md).

## Where a decision binds

`POST /v1/authorize` decides; the caller executes. The MCP gateway, the
OpenAI-compatible gateway, and the tool broker bind because they hold the
credential and execute only on ALLOW. A pre-tool hook binds for the tools it
sees. An editor integration binds nothing and says so.

## Evidence

Every decision is a hash-chained, HMAC-signed audit event carrying the full
trace (`agent-plane.trace.v1`). `GET /v1/decisions/{id}` returns it; the
console's decision drawer renders the chain, then a plain-English "why".
