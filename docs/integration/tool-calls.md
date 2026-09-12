# Tool calls

Three ways to govern a tool call, and they compose.

## 1. Report it from a connector

The ordinary path. A pre-tool hook, the MCP gateway, or the SDK reports the
tool to `POST /v1/events/action`; agent-plane normalizes it into a canonical
action and resource, evaluates the project's rules, and answers. Nothing is
wrapped by hand. See [connectors](../connectors.md).

## 2. Authorize in your executor (task authority)

The primitive. Before the executor performs a side effect it asks
`POST /v1/authorize` with the task, action, and canonical resource, and
proceeds only on ALLOW. This is where leases, protected resources, use limits,
expiry, and approvals apply. The [framework adapters](frameworks.md) add the
call to LangChain/CrewAI tools, OpenAI Agents hooks, or a custom dispatch in
one line; [authorization.md](authorization.md) covers the raw HTTP contract.

## 3. Broker the execution (`/v1/tools/invoke`)

The broker runs operator-registered tools with the **broker's** credential, so
the agent never holds the real API key. Tools are declared in
`config/tools.yaml`:

```yaml
tools:
  - name: send_external_email
    type: http
    url: https://mail.internal/api/send
    secret_env: MAIL_API_KEY        # read by the broker, never by the agent
```

```python
r = httpx.post(f"{PLANE}/v1/tools/invoke",
               headers={"Authorization": f"Bearer {agent_token}"},
               json={"tool": "send_external_email", "arguments": {...}})
# 200 result · 202 approval required (policy) · 403 denied_by_policy · 404 unknown_tool
```

This edge authenticates with an **agent identity token**, not a Project API
Key. It checks tool policy (`policies/*.yaml`, e.g.
`sensitive-tool-approval.yaml`) and the identity's capability manifest
(`allowed_tools`). It does **not** evaluate rules or task leases. To get both,
call `/v1/authorize` for the same operation first, then invoke the broker, and
make sure the agent cannot reach the broker or the target without going
through that executor.

## Choosing

| Situation | Use |
| --- | --- |
| Claude Code, Codex, an editor agent | a [connector](../connectors.md); no code |
| Agent is an MCP client | the [gateway](mcp-gateway.md) |
| Custom Python/TS agent loop | the SDK, or adapters + `/v1/authorize`; keep credentials in the executor |
| Agents must never see API keys and tools are HTTP-shaped | the broker, plus authorize in the dispatcher |
| Third-party framework with a central tool hook | an [adapter](frameworks.md) at that hook |
