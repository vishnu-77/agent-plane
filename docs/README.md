# agent-plane documentation

agent-plane is a control plane for what AI agents do. You connect the agents
you already run, watch what they actually do, write rules in plain terms, and
then decide how much of that is binding.

Start here if you have never run it:

| I want to… | Read |
| --- | --- |
| Get an agent reporting in five minutes | [Quickstart](quickstart.md) |
| Understand the product before installing it | [Concepts](concepts.md) |
| Understand accounts, projects and API keys | [Accounts, projects, keys](accounts.md) |
| Connect Claude Code, Codex, Cursor, MCP, or my own code | [Connectors](connectors.md) |
| Write authority rules (allow / ask first / never) | [Rules](rules.md) |
| Decide between observe, govern, and enforce | [Runtime modes](modes.md) |
| Roll out without blocking anyone first | [Observe → Govern → Enforce](integration/observe-enforce.md) |
| See it work without connecting anything | [Hosted demo](demo.md) |
| Look up an endpoint | [API reference](api-reference.md) · [openapi.json](openapi.json) |
| Run my own instance | [Deployment](deployment.md) · [deploy/](../deploy/README.md) |

## The one-paragraph model

You sign up in the console and create a **project**. The project issues
**API keys**. A **connector** (a coding-agent hook, the MCP gateway, or the
SDK in your own code) uses that key to report what an agent is about to do.
agent-plane turns each report into a canonical action and resource, evaluates
it against the project's **rules**, records a signed decision, and answers.
The project's **mode** decides what that answer means: `observe` records and
never blocks, `govern` returns the real decision but leaves execution to you,
`enforce` binds wherever the connector is actually able to stop the action.

No admin token, no hand-written YAML, and no policy bundle is needed to get
there. Those exist, and they are documented below, but they are what the
product runs on - not what you start with.

## Underneath, and for advanced use

The developer-facing rule compiles into an **AuthorityLease**: the internal
grant the decision engine has always evaluated. Everything below operates on
that layer directly. You do not need any of it to adopt agent-plane.

| Topic | Page |
| --- | --- |
| Leases, delegation, the raw `/v1/authorize` contract | [Task authorization](integration/authorization.md) |
| Human-in-the-loop approvals | [Approvals](integration/approvals.md) |
| Which integration edge fits which stack | [Integration overview](integration/README.md) |
| LangChain / CrewAI / OpenAI Agents / custom loops | [Framework adapters](integration/frameworks.md) |
| Proving your executor fails closed | [Conformance kit](integration/conformance.md) |
| Model calls through an OpenAI-compatible gateway | [Model calls](integration/model-calls.md) |
| Brokered tool calls without giving agents credentials | [Tool calls](integration/tool-calls.md) |
| Identity-filtered retrieval | [Retrieval](integration/retrieval.md) |
| The MCP gateway in production | [MCP gateway](integration/mcp-gateway.md) |
| The lease object and every reason code | [spec/authority-lease.md](../spec/authority-lease.md) |
| Trust boundaries | [SECURITY.md](../SECURITY.md) · [ARCHITECTURE.md](../ARCHITECTURE.md) |
| Per-edge design notes | [architecture/edges.md](architecture/edges.md) |

SDKs: [Python](../sdk/python/README.md) · [TypeScript](../sdk/typescript/README.md).

Longer reads: [capstone](capstone/control-plane-capstone.md) and
[series](series/README.md) - narrative background, not reference material.
