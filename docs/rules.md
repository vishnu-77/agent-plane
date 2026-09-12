# Rules

A rule is the authority model a developer actually writes. It says three
things about a set of canonical actions:

```text
ALLOW       repository.read  filesystem.read  filesystem.write  tests.execute
ASK FIRST   git.push  package.install  network.access
NEVER       repository.delete  credentials.read  filesystem.delete
```

Rules are per project. Nothing else has to be written: you never author a
lease document, and there is no policy bundle in this path.

## Shape

| Field | Meaning |
| --- | --- |
| `name` | what the rule is for |
| `scope` | `{agents, integrations, environments}`, each a list of glob patterns; `["*"]` or empty means everyone |
| `allow` | action patterns that may run |
| `ask` | in scope, but a human decides |
| `never` | refused, whatever else grants it |
| `resources` | resource patterns the rule reaches; defaults to `["*"]` |
| `protected_resources` | carve-outs inside that scope that are always refused |
| `max_uses` | `{action: n}` ceilings |
| `permitted_consequence` | bounds on what the action may cause |
| `enabled` | a disabled rule grants nothing |
| `order` | display order only; it does not change the decision |

Action and resource patterns are globs (`*.read`, `workspace/*`,
`github://acme/*`).

## How rules become decisions

Every governed decision compiles the project's **enabled** rules whose scope
matches the acting agent, integration, and environment into one ephemeral
`AuthorityLease`, cached under a deterministic id
(`rules:<project>:<agent>:<task>`) with a one-hour TTL:

```text
allow ∪ ask     -> lease.actions            what may be asked for at all
ask             -> lease.require_approval   ALLOW becomes APPROVAL_REQUIRED
never           -> lease.denied_actions     absolute refusal
resources       -> lease.resources
protected_…     -> lease.protected_resources
max_uses        -> lease.max_uses           narrowest ceiling across rules wins
permitted_…     -> lease.permitted_consequence (narrowest bound wins)
```

The fingerprint of the applicable rules is stored on the lease. Edit a rule
and the compiled lease is rebuilt on the next decision. Disable or delete the
last applicable rule and the compiled lease is **revoked**, so turning a rule
off takes authority away instead of leaving a stale grant behind.

A project with no applicable rule compiles to no lease at all. That is
default-deny: in `enforce` the answer is `DENY / NO_ACTIVE_LEASE`; in
`observe` it comes back as `simulate`. This is why a new project starts in
observe.

Rules and explicitly issued leases are evaluated together. That is what lets
a rule's NEVER refuse an action some other grant would have allowed.

## NEVER is absolute

`denied_actions` is checked across every active grant **before** scope, use
limits, and approval. Nothing grants it back: not another rule, not a lease
issued through the admin API, not a delegated child lease. The decision's
reason is `ACTION_REFUSED_BY_RULE`, and its explanation says so:

```text
A rule for this project lists repository.delete as NEVER allowed.
A never-rule is absolute: no other rule, lease, or delegation can grant it back.
```

## ASK FIRST

An action in `ask` returns `APPROVAL_REQUIRED` (HTTP 202 on `/v1/authorize`)
with reason `ACTION_REQUIRES_APPROVAL`.

In **enforce** mode that opens a tracked approval request; a human approves
or rejects it and the executor resumes once with the approval id. See
[approvals](integration/approvals.md).

In **observe** and **govern** mode no approval request is created - there is
nothing to resume, because nothing was stopped.

## Where rules come from

**Templates.** `config/rule-templates.yaml` ships starter sets (for example
`coding-agents` and `read-only`), offered in the Rules screen and returned in
`templates` by `GET /v1/rules?project=...`. Nothing is applied automatically:
a human picks one and reviews it.

**Suggestions.** `GET /v1/rules/suggested?project=...&agent=...` drafts a rule
from what agents actually did:

- read-shaped verbs (`read`, `list`, `get`, `search`, `describe`, `view`)
  are proposed as ALLOW;
- destructive verbs (`delete`, `destroy`, `remove`, `drop`, `purge`) as NEVER;
- everything else as ASK FIRST;
- actions already covered by an enabled rule for that agent are left out, so
  a suggestion only ever concerns behaviour nobody has ruled on.

Each draft carries `basis: {observed, denied}` - the counts it was built
from. A suggestion is a draft, not authority: it becomes a rule only when a
human saves it, with `source: "suggested"` recorded.

**By hand.** The Rules screen, or the API.

## API

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/v1/rules?project=` | session, `ap_mgmt_` key, admin token, demo token | rules, the action vocabulary the editor offers, and the templates |
| POST | `/v1/rules` | console session | create; body carries `project` plus the fields above |
| PATCH | `/v1/rules/{id}` | console session | update any of `name`, `allow`, `ask`, `never`, `resources`, `protected_resources`, `max_uses`, `permitted_consequence`, `enabled`, `order`, `scope` |
| DELETE | `/v1/rules/{id}` | console session | delete |
| GET | `/v1/rules/suggested?project=&agent=` | session, `ap_mgmt_` key, admin token, demo token | drafts from observed activity |

```bash
curl -X POST http://127.0.0.1:8000/v1/rules \
  -H 'Content-Type: application/json' -b "ap_session=$COOKIE" \
  -d '{"project": "prj_…", "name": "Coding agents",
       "scope": {"agents": ["*"]},
       "allow": ["repository.read", "filesystem.read", "filesystem.write", "tests.execute"],
       "ask": ["git.push", "package.install"],
       "never": ["repository.delete", "credentials.read"],
       "resources": ["workspace/*", "github://*"],
       "protected_resources": ["workspace/.env*", "github://*/branches/main"]}'
```

Every create, update, and delete is written to the audit chain as an
`admin_action` event.

## Permissions as a file

Rules can live in version control next to the code they govern. The format is
the one the Rules screen speaks, so a rule written in the console exports
unchanged and a template can be pasted in as it is.

```yaml
# agent-plane permissions.
version: 1
rules:
  - name: Coding agents
    scope:
      agents: ["claude-code", "codex"]
    allow: [filesystem.read, tests.execute, git.commit]
    ask: [filesystem.write, git.push]
    never: [repository.delete, credentials.read]
    resources: ["workspace/*", "github://acme/app"]
    protected_resources: ["workspace/.env*"]
```

Only `name` and at least one of `allow` / `ask` / `never` are required.
Anything left out keeps its default, and an unknown field is an error rather
than something quietly ignored: a permissions file that is almost right is
worse than one that is obviously wrong.

| | |
| --- | --- |
| `agentplane rules check permissions.yaml` | validate the file; no credential, nothing changed |
| `agentplane rules pull --project prj_… --key ap_mgmt_…` | write the project's permissions to stdout |
| `agentplane rules push permissions.yaml --project prj_… --key ap_mgmt_…` | apply it |

`push` merges by rule name: it creates what is new, updates what a rule of the
same name already says, and leaves anything the file does not mention alone.
`--replace` makes the file the whole truth for the project and deletes the
rest. That is usually what a pipeline wants, and it is never the default
because it throws away rules someone wrote in the console.

The same two operations are `GET /v1/rules/export?project=` and
`POST /v1/rules/import`, which accept a console session or a management key.
A project key (`ap_live_`) reports actions; it cannot rewrite the authority it
is judged by. In the console the file is under **Permissions as a file** on the
Rules screen.

`agentplane rules check` exits non-zero on a file it cannot read, so it belongs
in the same pull request that changes the file.

## The layer underneath

`AuthorityLease` has not gone anywhere - it is still what the engine
evaluates, what delegation attenuates, and what lineage is drawn from. Rules
are simply how it is authored now. If you need to issue a lease directly, bind
one agent to one task by hand, or delegate an attenuated child lease, that is
[task authorization](integration/authorization.md) and it requires the
deployment's admin token.
