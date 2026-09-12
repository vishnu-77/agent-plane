# agent-plane documentation

Start here. Each page is written for one job.

| I want to… | Read |
| --- | --- |
| See it work in five minutes | [Quickstart](quickstart.md) |
| Understand which integration edge fits my stack | [Integration overview](integration/README.md) |
| Gate side-effecting actions on task authority | [Task authorization](integration/authorization.md) |
| Put a human in the loop for risky actions | [Approvals](integration/approvals.md) |
| Wrap LangChain / CrewAI / OpenAI Agents / a custom loop | [Framework adapters](integration/frameworks.md) |
| Prove my executor fails closed | [Conformance kit](integration/conformance.md) |
| Govern model calls with zero code change | [Model calls](integration/model-calls.md) |
| Broker tool calls without giving agents credentials | [Tool calls](integration/tool-calls.md) |
| Filter retrieval by identity | [Retrieval](integration/retrieval.md) |
| Put agent-plane in front of an MCP server | [MCP gateway](integration/mcp-gateway.md) |
| Look up an endpoint | [API reference](api-reference.md) · [openapi.json](openapi.json) |
| Run it in production | [Deployment](deployment.md) · [deploy/](../deploy/README.md) |
| Understand the lease object and reason codes | [spec/authority-lease.md](../spec/authority-lease.md) |
| Understand the trust boundaries | [SECURITY.md](../SECURITY.md) · [ARCHITECTURE.md](../ARCHITECTURE.md) |

SDKs: [Python](../sdk/python/README.md) · [TypeScript](../sdk/typescript/README.md).

## The one-paragraph model

agent-plane is a standalone service beside your agent stack. Your trusted
backend issues a **lease** binding one agent to one task with a narrow set of
actions and resources. Your trusted executor asks `POST /v1/authorize` before
each side-effecting action and runs it **only on ALLOW**. APPROVAL REQUIRED
opens an approval request a human decides on; the executor resumes with the
approval id and gets ALLOW exactly once. Operators can shrink or revoke a live
lease without touching the underlying credential. Every decision is a signed
audit record you can read back through the API or the console.

## Longer reads

- [docs/architecture/edges.md](architecture/edges.md) - per-edge design notes.
- [docs/capstone](capstone/control-plane-capstone.md) and [docs/series](series/README.md) - narrative background.
