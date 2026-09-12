# Integration overview

agent-plane sits beside your agent stack; it does not intercept anything on
its own. Pick the edges you need. They share identity, policy, and one signed
audit chain, but their coverage differs, and that difference matters.

| Edge | What you change | What agent-plane checks | Page |
| --- | --- | --- | --- |
| Model calls | `base_url` swap on the OpenAI-compatible client | policy allow/deny/redact, quotas, routing, audit | [model-calls.md](model-calls.md) |
| Tool calls (broker) | route tool execution through `/v1/tools/invoke` | tool policy, capability manifest; broker holds the credential | [tool-calls.md](tool-calls.md) |
| Retrieval | route vector-store reads through `/v1/retrieve` | document ACLs by tenant/department/classification | [retrieval.md](retrieval.md) |
| **Task authority** | `POST /v1/authorize` before each side-effecting action | lease scope, protected resources, use limits, expiry, approvals | [authorization.md](authorization.md) |
| Approvals | operator decides, executor resumes | one-shot, lease-bound, audited | [approvals.md](approvals.md) |
| MCP gateway | point the MCP client at `/mcp` | admission before dispatch for mapped tools; upstream credential separation | [mcp-gateway.md](mcp-gateway.md) |
| Delegation | `POST /v1/leases/{id}/delegate` | child lease never wider than the parent | [authorization.md](authorization.md#delegation) |

Only `/v1/authorize` and the MCP gateway evaluate task leases. Model, broker,
and retrieval routes apply policy and capability checks but do not consult
leases; add the authorize call in your executor where a task-scoped decision
is needed.

## Roles and credentials

| Credential | Held by | Used for |
| --- | --- | --- |
| Agent bearer token | trusted executor acting for one agent | `/v1/authorize`, `/v1/approvals/{id}` (own), `/v1/leases/{id}/delegate`, model/tool/retrieval edges |
| `ADMIN_TOKEN` (`X-Admin-Token`) | trusted backend, operator tooling, console | issue/shrink/revoke leases, approval queue, audit, policy reload |
| Target credentials (GitHub, cloud, …) | executor or broker adapter only | the real operation |

The agent must not hold target credentials or a direct network route to the
target, or the authorization check is advisory. Bind the task id to your
authenticated workflow; never accept a task chosen by the model.

## Where the wrapper goes

There is no universal interception point across agent frameworks, so the
authorize call goes into the one place your code executes tools:

- a central `dispatch(tool_name, arguments)` in a custom loop → [`governed_dispatch`](frameworks.md#custom-loops);
- each LangChain / CrewAI tool → [`langchain_tool` / `crewai_tool`](frameworks.md#langchain-and-crewai);
- an OpenAI Agents SDK tool guardrail → [`openai_agents_guard`](frameworks.md#openai-agents-sdk);
- any plain function → the [`@govern`](frameworks.md#plain-functions) decorator;
- MCP-speaking agents → the [gateway](mcp-gateway.md), which needs no wrapper.

Then run the [conformance kit](conformance.md) against the wrapper before
connecting real side effects.
