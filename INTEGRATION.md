# Integrating agent-plane into an existing product

This page moved. The integration documentation now lives under
[docs/integration](docs/integration/README.md), one page per edge:

- [Overview: which edge fits your stack](docs/integration/README.md)
- [Task authorization (`/v1/authorize`, leases, delegation)](docs/integration/authorization.md)
- [Approvals: human in the loop](docs/integration/approvals.md)
- [Framework adapters: LangChain, CrewAI, OpenAI Agents, custom loops](docs/integration/frameworks.md)
- [Conformance kit: prove the executor fails closed](docs/integration/conformance.md)
- [Model calls (zero code change)](docs/integration/model-calls.md)
- [Tool calls (broker)](docs/integration/tool-calls.md)
- [Retrieval](docs/integration/retrieval.md)
- [MCP gateway](docs/integration/mcp-gateway.md)

Short answer to "is it plug-and-play": model calls, yes (a `base_url` swap).
MCP agents, yes (point the client at `/mcp`). Everything else is one wrapper
at your tool-dispatch point, which the SDK adapters provide.

Start with the [quickstart](docs/quickstart.md).
