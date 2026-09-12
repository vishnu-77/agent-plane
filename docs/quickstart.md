# Quickstart

From a clean checkout to a coding agent reporting what it does. Nothing is
blocked along the way: a new project starts in **observe** mode.

You need Python 3.11+. You do not need a model provider key, an admin token,
or any YAML.

## 1. Run the service

```bash
python -m venv .venv && source .venv/bin/activate     # PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]" -e ./sdk/python
agentplane serve --host 127.0.0.1 --port 8000
```

In `ENVIRONMENT=development` (the default) the service starts with no
configuration. Accounts, projects, keys, rules, leases, and the signed audit
chain all live in `audit.db` (SQLite) and survive restarts.

Open <http://127.0.0.1:8000/console>.

## 2. Sign up

The first account on a fresh install owns the workspace, and sign-up closes
behind it (`SIGNUP_MODE=first_user`, the default). Passwords are at least ten
characters.

Signing in sets an httponly `ap_session` cookie. The console never asks you to
paste a token, and the cookie never authorizes an agent action - only a human
session.

## 3. Create a project

A project is one governed environment: its own keys, integrations, rules,
mode, and activity. Name it after what it governs (`checkout-api`,
`my-laptop`) and leave the mode at **observe**.

## 4. Choose an integration and generate a key

Go to **Integrations** and pick what you actually run. The connect dialog
creates a Project API Key for you and shows it **once** - the server stores
only an HMAC of it.

Keys start with `ap_live_`, `ap_test_`, or `ap_mgmt_`. See
[accounts.md](accounts.md).

## 5. Connect

The dialog prints the exact command. For Claude Code:

```bash
agentplane connect claude --key ap_live_...
```

It verifies the key against the running service, stores it in
`~/.agentplane/credentials.json` (not in a file you commit), installs a
`PreToolUse` hook in `~/.claude/settings.json` (`--scope project` writes
`.claude/settings.json` instead), and prints what the integration can observe
and whether it can enforce.

The other targets are `codex`, `cursor`, `mcp`, and `sdk`. Check any of them
with `agentplane connect status`, and undo one with
`agentplane connect disconnect claude`. See [connectors.md](connectors.md).

## 6. Watch activity appear

Use the agent normally. Each tool call it makes is reported to
`POST /v1/events/action`, normalized into a canonical action and resource
(`Bash` running `git push` becomes `git.push`), decided, and recorded.

**Activity** in the console fills up. Every row opens a decision: what the
agent did, what it would affect, what decided it, and why. In observe mode
nothing is blocked and the hook always exits 0 - including when agent-plane
is unreachable.

By default only metadata is collected: agent, session, task, action,
resource, decision, and consequence. Prompt text, tool arguments, tool
output, and file contents are off unless you turn them on per project under
**Settings → Data collection**.

## 7. Understand what you saw

**Rules → Suggested** (`GET /v1/rules/suggested?project=...`) drafts a rule
from what each agent actually did: read-shaped actions proposed as ALLOW,
writes as ASK FIRST, deletes as NEVER. Nothing is applied automatically.

Review a draft, edit it, and save it. Or start from a template in
**Rules → Templates**. A rule says three things:

```text
ALLOW       read the repository, modify the workspace, run tests
ASK FIRST   push git changes, install packages
NEVER       delete repositories, read credentials
```

NEVER is absolute: no other rule, lease, or delegation can grant it back.
See [rules.md](rules.md).

## 8. Govern, then enforce

**Settings → Mode**, or `PATCH /v1/projects/{id}` with `{"mode": "govern"}`.

| Mode | What comes back | What binds |
| --- | --- | --- |
| `observe` | a would-be DENY or ASK returns `simulate` with `would_be` | nothing |
| `govern` | the real decision, with `enforced: false` | nothing; your executor decides |
| `enforce` | the real decision, with `enforced: true` | wherever the connector can stop the action |

Move to `enforce` when every agent in the project is covered by a rule you
have reviewed. What enforcement means depends on the connector: the MCP
gateway refuses to dispatch, a pre-tool hook blocks the tool, an editor
integration can only report. The response says which, in `enforcement` and
`binding` - nothing implies agent-plane blocked what a connector cannot
block. See [modes.md](modes.md).

## Instead of connecting an agent: use the SDK

```bash
pip install ./sdk/python
export AGENTPLANE_API_KEY=ap_live_...
export AGENTPLANE_URL=http://127.0.0.1:8000
```

```python
from agentplane import AgentPlane

ap = AgentPlane()                                   # reads the environment
with ap.task("fix-authentication-tests") as task:
    decision = task.authorize("filesystem.write", "workspace/src/auth.ts")
    if decision.proceed:                            # ALLOW, or SIMULATE in observe mode
        write_the_file()
        task.report(tool="Edit", resource="workspace/src/auth.ts")
```

`decision.allowed` stays strict (ALLOW only); `decision.proceed` also accepts
an observe-mode `simulate`, which is not enforced. See
[connectors.md](connectors.md#your-own-code-sdk).

## Next

- [Concepts](concepts.md) - the model in one page.
- [Observe → Govern → Enforce](integration/observe-enforce.md) - the rollout.
- [Deployment](deployment.md) - running your own instance for a team.
