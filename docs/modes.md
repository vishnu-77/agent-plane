# Runtime modes

Every project is in exactly one of three modes. The mode does not change what
agent-plane decides - the decision, the trace, and the signed audit record are
computed identically in all three. It changes what the decision *means* to the
caller.

| Mode | The engine decides | The caller is told | Anything blocked? |
| --- | --- | --- | --- |
| `observe` | in full | a would-be DENY or ASK comes back as `simulate` | no |
| `govern` | in full | the real outcome, with `enforced: false` | no |
| `enforce` | in full | the real outcome, with `enforced: true` | yes, where the connector can |

A project created through onboarding starts in `observe`.

## observe

A decision that would have been `deny` or `approval_required` is returned as
`simulate` (HTTP 200) with `enforced: false` and `would_be` set to what
enforce mode would have returned. `allow` stays `allow`.

```json
{"decision": "simulate", "reason": "NO_ACTIVE_LEASE", "mode": "observe",
 "enforced": false, "would_be": "deny", "advisory": true,
 "explanation": ["Observe mode: this would have been DENY under enforcement. …"]}
```

Nothing is stopped, and nothing a developer connects can break because
agent-plane is watching. Every attempt is recorded on the audit chain and in
the registry exactly as it would be under enforcement, so the evidence you
review later is real evidence.

A use limit is consumed only on an `allow`.

In the SDK: `decision.enforced` is `false`, `decision.proceed` is `true`,
`decision.allowed` stays `false`. The adapters run the tool on `proceed`, so
an executor in observe mode behaves as if agent-plane were not there.

## govern

The real outcome is returned - `deny` is still `deny` (HTTP 403 on
`/v1/authorize`), `approval_required` is still `approval_required` (HTTP 202)
- but `enforced` is `false`, `would_be` is set, and `advisory` is `true`. The
executor is being told the truth and is free to proceed anyway.

Two consequences of nothing being binding:

- **No approval request is created.** `approval_required` in govern mode has
  no `approval_id` and nothing to resume; it is a signal, not a gate.
- **No use limit is consumed**, for any outcome.

Govern is for the period when the rules are written but you are not yet
willing to have them stop work - or for a connector that could never stop
anything anyway.

## enforce

The decision binds wherever agent-plane is actually in the path:

- `allow` (200) - execute exactly this action;
- `deny` (403) - do not execute;
- `approval_required` (202) - a tracked approval request is opened; a human
  approves or rejects it, and the executor resumes once with the approval id;
- `quarantine` (423) - an operator is holding this agent.

A use limit is consumed on `allow`, and also on `approval_required`.

Enforce is the only mode where a `PreToolUse` hook returns exit 2 and where
the MCP gateway refuses to dispatch.

## Quarantine ignores the mode

Quarantine is an operator's explicit hold on one agent. It is checked before
anything else and is returned as `quarantine` (HTTP 423) in **every** mode,
including observe.

```bash
curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
  "http://127.0.0.1:8000/v1/agents/support-bot/quarantine?tenant=prj_…" \
  -H 'Content-Type: application/json' -d '{"note": "exporting customer data"}'
```

A console session, a management key, or `ADMIN_TOKEN` all work here; the last
two go in `X-Admin-Token`. Release with `DELETE` on the same path.

## What the connector can do about it

Enforce mode is a statement about agent-plane, not about the connector. A
decision only stops something if the connector is able to stop it, so every
response also carries:

| Field | Meaning |
| --- | --- |
| `enforcement` | the integration's declared capability: `full`, `partial`, or `advisory` |
| `binding` | `true` only when the decision was enforced **and** `enforcement` is `full` or `partial` |

`binding: false` on a `deny` in enforce mode is not a bug: it means this
connector reported an action it cannot stop. See
[connectors.md](connectors.md).

## Changing the mode

In the console: **Settings → Mode**. Over the API:

```bash
curl -X PATCH http://127.0.0.1:8000/v1/projects/prj_… \
  -H 'Content-Type: application/json' -b "ap_session=$COOKIE" \
  -d '{"mode": "govern"}'
```

The change is recorded on the audit chain and takes effect on the next
decision. Connectors do not need to reconnect; `agentplane connect status`
shows the current mode.

### The deployment-level fallback

`ENFORCEMENT_MODE` (default `enforce`) and `PUT /admin/mode` apply only to
traffic that belongs to no project - a legacy JWT tenant on an upgraded
deployment. A project's own mode always wins. `PUT /admin/mode` accepts only
`observe` and `enforce`; `govern` is a project-level mode.
