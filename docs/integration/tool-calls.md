# Tool calls

Two ways to govern a tool call, and they compose.

## 1. Authorize in your executor (task authority)

The primitive. Before the executor performs a side effect, it asks
`POST /v1/authorize` with the task, action, and canonical resource, and
proceeds only on ALLOW. This is where leases, protected resources, use
limits, expiry, and approvals apply. The [framework adapters](frameworks.md)
add the call to LangChain/CrewAI tools, OpenAI Agents hooks, or a custom
dispatch in one line; the [authorization guide](authorization.md) covers the
raw HTTP contract.

## 2. Broker the execution (`/v1/tools/invoke`)

The broker runs operator-registered tools with the **broker's** credential,
so the agent never holds the real API key. Tools are declared in
`config/tools.yaml`:

```yaml
tools:
  - name: send_external_email
    type: http
    url: https://mail.internal/api/send
    secret_env: MAIL_API_KEY        # read by the broker, never by the agent
```

```python
r = httpx.post(f"{PLANE}/v1/tools/invoke", headers={"Authorization": f"Bearer {token}"},
               json={"tool": "send_external_email", "arguments": {...}})
# 200 result · 202 approval required (policy) · 403 denied_by_policy · 404 unknown_tool
```

The broker checks tool policy (`policies/*.yaml`, e.g.
`sensitive-tool-approval.yaml`) and the identity's capability manifest
(`allowed_tools`). It does **not** evaluate task leases. To get both, call
`/v1/authorize` for the same operation first, then invoke the broker, and make
sure the agent cannot reach the broker or the target without going through
that executor.

## 3. MCP

If the agent speaks MCP, the [gateway](mcp-gateway.md) does admission and
dispatch in one place and needs no wrapper in the agent.

## Choosing

| Situation | Use |
| --- | --- |
| Custom Python/TS agent loop | adapters + `/v1/authorize`; keep credentials in the executor |
| Agents must never see API keys and tools are HTTP-shaped | broker (+ authorize in the dispatcher) |
| Agent is an MCP client | gateway |
| Third-party framework with a central tool hook | adapter at that hook |
