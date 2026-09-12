# Integration overview

This directory is the **advanced** material: the edges agent-plane can sit on,
and the raw contracts behind them.

If you are adopting agent-plane for the first time, you do not need any of it.
Sign up, create a project, connect an integration, and write rules:
[quickstart](../quickstart.md) - [connectors](../connectors.md) -
[rules](../rules.md) - [modes](../modes.md).

## The edges

agent-plane sits beside your agent stack; it does not intercept anything on
its own. Each edge shares identity, rules, and one signed audit chain, but
their coverage differs, and that difference matters.

| Edge | What you change | What agent-plane checks | Page |
| --- | --- | --- | --- |
| **Reported activity** | connect a connector, or call the SDK | rules compiled to a lease, resource scope, consequence | [connectors](../connectors.md) |
| **Task authority** | `POST /v1/authorize` before each side-effecting action | lease scope, protected resources, use limits, expiry, approvals | [authorization.md](authorization.md) |
| MCP gateway | point the MCP client at `/mcp` | admission before dispatch for mapped tools; upstream credential separation | [mcp-gateway.md](mcp-gateway.md) |
| Approvals | operator decides, executor resumes | one-shot, lease-bound, audited | [approvals.md](approvals.md) |
| Model calls | `base_url` swap on the OpenAI-compatible client | policy allow/deny/redact, quotas, routing, audit | [model-calls.md](model-calls.md) |
| Tool calls (broker) | route tool execution through `/v1/tools/invoke` | tool policy, capability manifest; the broker holds the credential | [tool-calls.md](tool-calls.md) |
| Retrieval | route vector-store reads through `/v1/retrieve` | document ACLs by tenant/department/classification | [retrieval.md](retrieval.md) |
| Delegation | `POST /v1/leases/{id}/delegate` | a child lease is never wider than its parent | [authorization.md](authorization.md#delegation) |

Only `/v1/events/action`, `/v1/authorize`, and the MCP gateway evaluate task
authority. The model, broker, and retrieval routes apply policy files and
capability checks but do not consult rules or leases; add the authorize call
in your executor where a task-scoped decision is needed.

## Roles and credentials

| Credential | Held by | Used for |
| --- | --- | --- |
| Project API Key (`ap_live_`) | a connector, or your executor | `/v1/events/action`, `/v1/authorize`, `/v1/sessions`, `/v1/tasks` |
| Management key (`ap_mgmt_`) | automation that reads one project | activity, agents, decisions, rules, approvals |
| Console session | a human in the browser | everything a project owner does |
| `ADMIN_TOKEN` | the deployment operator | direct lease issuance, policy reload, credential revocation, every tenant |
| Agent identity token (JWT / delegation) | a trusted executor that mints its own | the same runtime routes, plus the model/tool/retrieval edges |
| Target credentials (GitHub, cloud, …) | the executor or broker adapter only | the real operation |

The agent must not hold target credentials or a direct network route to the
target, or the authorization check is advisory whatever the project mode says.
Bind the task id to your authenticated workflow; never accept a task chosen by
the model.

## Where the wrapper goes

For coding agents and MCP clients, nothing is wrapped: the
[connector](../connectors.md) reports from a pre-tool hook or from the gateway
itself.

For your own code, there is no universal interception point across agent
frameworks, so the authorize call goes into the one place your code executes
tools:

- a central `dispatch(tool_name, arguments)` in a custom loop -> [`governed_dispatch`](frameworks.md#custom-loops);
- each LangChain / CrewAI tool -> [`langchain_tool` / `crewai_tool`](frameworks.md#langchain-and-crewai);
- an OpenAI Agents SDK tool guardrail -> [`openai_agents_guard`](frameworks.md#openai-agents-sdk);
- any plain function -> the [`@govern`](frameworks.md#plain-functions) decorator.

Then run the [conformance kit](conformance.md) against the wrapper before
connecting real side effects.
