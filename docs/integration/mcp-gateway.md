# MCP gateway

For agents that speak MCP, the gateway is the closest thing to drop-in: point
the client at agent-plane's `/mcp`, and every `tools/call` is admitted against
capability, policy, the trusted task binding, and the current authority before
it is dispatched to the upstream with the gateway's own credential. The agent
never sees the upstream URL or token.

This is the only integration whose enforcement is declared `full`: agent-plane
holds the credential, so a refused tool is never dispatched.

The implementation notes, limits, and the runnable demo live in
[spec/mcp-gateway-preview.md](../../spec/mcp-gateway-preview.md). This page is
the operator's path to production.

## 0. Register the integration

```bash
agentplane connect mcp --key ap_live_... --upstream https://mcp.example.com/mcp
```

This verifies the key, registers the `mcp` integration on the project, stores
the credential, and prints the client configuration and the discovery command.
It does **not** configure the gateway by itself: steps 1-3 below still apply.

## 1. Generate the mapping file

```bash
export UPSTREAM_TOKEN=...
agentplane mcp discover \
  --upstream https://mcp.example.com/mcp --secret-env UPSTREAM_TOKEN \
  --tenant prj_… --agent repo-agent --task cleanup --lease lease-cleanup \
  --prefix repo --resource-prefix github://acme/repo \
  --out config/mcp-gateway.yaml
```

Discovery lists the upstream's tools and writes one mapping per tool: public
name and action `repo.<tool>`, the upstream schema tightened to
`additionalProperties: false`, and a resource template using the first
required string argument. **Review it.** Delete tools the agent must never
reach, fix resource templates so protected-resource patterns match what you
intend, and make sure each `action` is granted by the project's rules or by
the bound lease.

## 2. Bind agent, task, and authority

The `bindings` list fixes one task and one lease per `(tenant, agent)`. Issue
the lease (`POST /v1/leases` or a template) before starting the gateway. The
`tenant` is the project id.

## 3. Enable

```dotenv
MCP_GATEWAY_FILE=/app/config/mcp-gateway.yaml
```

Install the `[mcp]` extra (included in `[all]` and the container image). The
client must send `MCP-Protocol-Version: 2026-07-28` and a bearer token.

`/mcp` accepts either credential: the Project API Key `agentplane connect mcp`
prints, or an agent identity token resolved through `IDENTITY_MODE`
(`jwt_claims` or `delegation`). Neither grants anything by itself. The caller's
`(tenant, agent)` pair must match a binding in the mapping file above, and
anything without one gets `401 invalid_identity_or_task_binding`.

With a key, the tenant is the key's project, so write the binding's `tenant` as
the project id (`agentplane mcp discover --tenant prj_...`) and send the agent
name in `X-Agent-Id`. An identity token carries the capability manifest its
issuer asserts; a key carries none, so the tools in this mapping file are the
manifest for a key-authenticated caller. To mint agent tokens instead, see
[authorization.md](authorization.md#2-credentials).

Because `/mcp` is mounted only when `MCP_GATEWAY_FILE` is set, it does not
appear in the committed [openapi.json](../openapi.json).

## 4. Run it for real

The gateway is supported in `ENVIRONMENT=production`:

- leases, use counters, and the request-key ledger are in the shared SQL
  store, so several gateway replicas behind one load balancer stay consistent,
  and a revocation is honoured by all of them;
- retried `tools/call`s with the same `_meta["agent-plane/request-id"]` return
  the recorded result from whichever replica handled the first attempt, and
  never dispatch twice;
- APPROVAL REQUIRED admissions open an [approval request](approvals.md); the
  client resumes with `_meta["agent-plane/approval-id"]`;
- decision, dispatch, completion, and unknown-outcome receipts are separate
  signed audit records, grouped in the console.

What is still true: a timeout after dispatch is **outcome unknown**, not proof
of no effect; one upstream per gateway file; no OAuth onboarding and no
automatic client rewrite.

## Verify

```bash
python examples/mcp_gateway_demo.py            # real MCP client, gateway, mock upstream
python examples/mcp_gateway_demo.py --serve    # then open /console
```
