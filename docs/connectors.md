# Connectors

A connector is what reports to agent-plane on behalf of an agent. Each one is
honest about two separate things:

- **observation** - how much of what the agent does it can see;
- **enforcement** - whether it can actually stop an action.

These are declared per integration kind in `INTEGRATION_CATALOG`
(`agent_plane/accounts/models.py`), returned by
`GET /v1/integrations?project=...`, and shown in the console. Every decision
returned to a connector carries `enforcement` and `binding`, so nothing ever
implies agent-plane blocked what a connector cannot block.

## The catalog

| Kind | Observation | Enforcement | What that means |
| --- | --- | --- | --- |
| `claude-code` | full | partial | Pre-tool hooks can block most actions; a tool that runs outside the hook is observed, not stopped. |
| `codex` | full | partial | Blocks where the CLI exposes a pre-execution hook; otherwise advisory. |
| `cursor` | partial | advisory | Reports what happened; it cannot interrupt the editor. |
| `mcp` | full | full | Every `tools/list` and `tools/call` passes through agent-plane, which holds the upstream credential. |
| `langgraph` | application-defined | advisory | Whatever your graph reports through the SDK; your code decides whether to honour a decision. |
| `gateway` | full | full | OpenAI-compatible and brokered tool traffic routed through agent-plane, which holds the provider credential. |
| `custom` | application-defined | advisory | Actions your application reports and authorizes through the SDK. |

`binding` in a decision response is `true` only when the decision was
enforced **and** the integration's enforcement is `full` or `partial`. An
advisory connector never gets `binding: true`, in any mode.

## The CLI

```bash
agentplane connect claude  --key ap_live_...
agentplane connect codex   --key ap_live_...
agentplane connect cursor  --key ap_live_...
agentplane connect mcp     --key ap_live_... --upstream https://mcp.example.com/mcp
agentplane connect sdk     --key ap_live_...
agentplane connect status
agentplane connect disconnect claude
```

Common options: `--url` (default `$AGENTPLANE_URL` or
`http://127.0.0.1:8000`), `--host`, and `--scope user|project` for where the
hook is installed. Only `ap_live_` and `ap_test_` keys are accepted.

Every target first calls `POST /v1/auth/exchange` to verify the key and
register the integration, then stores the credential in
`~/.agentplane/credentials.json` (owner-readable; override the directory with
`AGENTPLANE_HOME`). It is deliberately not written into an editor settings
file, which people commit by accident.

`agentplane connect status` prints the stored URL, integration, masked key,
project, and the project's current mode, and re-verifies against the server.

## Claude Code and Codex: the hook

`connect claude` merges a `PreToolUse` entry into `~/.claude/settings.json`
(or `.claude/settings.json` with `--scope project`) without disturbing
anything already configured. `connect codex` writes a `pre_tool_use` entry to
`~/.codex/hooks.json` or `.codex/hooks.json`.

Both point at `agentplane hook --integration <kind>`, which:

```text
stdin   {"tool_name": "Bash", "tool_input": {"command": "git push"}, ...}
exit 0  proceed
exit 2  blocked; stderr is shown to the agent and the user
```

The hook sends one event to `POST /v1/events/action`: the tool name and
argument shape, the workspace root, the git repository and branch, the host,
the session, and a task derived from what the agent knows (falling back to
the workspace directory name). A prompt, if the agent supplies one, is sent
as provenance and dropped by the server unless the project collects prompt
content.

It returns exit 2 **only** when the decision is `deny`, `quarantine`, or
`approval_required` **and** `binding` is true - which means enforce mode on
an integration that can block. In observe and govern mode it always exits 0
and writes what it saw to stderr. If agent-plane is unreachable or errors, it
exits 0 and says so: an observability tool must not wedge someone's editor.

Tool names are normalized on the server, from the name and the shape of the
arguments only - never from file contents or prompt text:

```text
Bash("git push")     -> git.push
Bash("pytest -q")    -> tests.execute
Edit / Write         -> filesystem.write
Read / Glob / Grep   -> filesystem.read
WebFetch             -> network.access
```

A connector that already knows the canonical action can send `action` and
`resource` directly, and nothing overrides it.

## MCP

`agentplane connect mcp --key ... --upstream ...` registers the integration
and prints the client configuration:

```json
{"mcpServers": {"agent-plane": {"url": "http://127.0.0.1:8000/mcp",
                                "headers": {"Authorization": "Bearer ap_live_..."}}}}
```

This is the one connector that is a true chokepoint: agent-plane holds the
upstream credential, so a refused tool is never dispatched. It needs more than
the connect command: a mapping file generated once with `agentplane mcp
discover`, `MCP_GATEWAY_FILE` set on the server, and a bound lease.

`/mcp` accepts the Project API Key the connector prints, and also the agent
identity tokens resolved through `IDENTITY_MODE` for deployments that mint
their own. Either way the caller's `(tenant, agent)` pair must match a binding
in the mapping file, so the key authenticates the caller and never grants it
anything: a request with no binding is `401 invalid_identity_or_task_binding`.
Send the agent name in `X-Agent-Id`.

An identity token carries the capability manifest its issuer asserts. A project
key carries none, and a manifest the client declared about itself would be
worth nothing, so the operator's tool mapping is the manifest: the gateway
exposes exactly the actions that file maps. The full path to production is
[integration/mcp-gateway.md](integration/mcp-gateway.md).

## Your own code (SDK)

`agentplane connect sdk --key ...` stores the credential and prints the two
environment variables. Then:

```python
from agentplane import AgentPlane

ap = AgentPlane()                         # AGENTPLANE_API_KEY / AGENTPLANE_URL
# or: AgentPlane(api_key="ap_live_...", url="https://plane.example.com",
#                agent="repo-agent", integration="langgraph")

with ap.task("fix-authentication-tests") as task:
    decision = task.authorize("filesystem.write", "workspace/src/auth.ts")
    if decision.proceed:
        write_the_file()
        task.report(tool="Edit", resource="workspace/src/auth.ts")
```

- `ap.task(name)` registers the task's provenance (`POST /v1/tasks`) and
  returns a handle. A failed registration never blocks the work.
- `task.authorize(action, resource)` asks before a side effect
  (`POST /v1/authorize`). `decision.allowed` is ALLOW only; `decision.proceed`
  also accepts an observe-mode `simulate`, which is not enforced.
- `task.report(...)` tells agent-plane what a tool did
  (`POST /v1/events/action`), letting the server normalize the tool name.

The SDK decides nothing itself. Your executor must hold the real credentials
and must not let the agent reach the target another way, or the check is
advisory whatever the mode says.

`AgentPlane` still accepts the older positional form
(`AgentPlane(url, agent_jwt)`) for deployments that mint their own identity
tokens. `AgentPlaneAdmin(url, admin_token)` is the operator client and is
covered under [task authorization](integration/authorization.md).

## Integrations in the API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/v1/integrations?project=` | connected integrations plus the full catalog |
| POST | `/v1/integrations` | `{project, kind, name?, host?, config?}`; console session |
| DELETE | `/v1/integrations/{id}?project=` | forget one; console session |

An integration's `status` is `pending` until the first event arrives, then
`connected`. `last_seen_at`, the agents seen, and an action count are updated
by every report.
