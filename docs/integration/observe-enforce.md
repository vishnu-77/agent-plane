# Observe, govern, enforce

Nobody can write a complete authority model for agents they have not watched.
So adoption starts by watching.

The exact semantics of the three modes are in [modes.md](../modes.md). This
page is the rollout.

```text
OBSERVE
  | discover agents             every reported action creates the agent record
  | discover tasks              a connector names the task; POST /v1/tasks records its origin
  | observe actions             requested vs exercised vs denied, per action
  | observe resources           what was actually touched, with its consequence profile
  | infer requested capability  declared (identity) vs observed drift
  | suggested rules             GET /v1/rules/suggested?project=...
  | review                      tighten scope, add protected resources, bound consequence
GOVERN
  | the real decision comes back, still not binding
  | fix what the rules get wrong while nothing is at stake
ENFORCE
```

## 1. Observe

A project created through onboarding is already in `observe`. Connect the
agents you run and use them normally for a few days.

Nothing is blocked. A decision that would have been DENY or ASK comes back as
`simulate` (HTTP 200) with `enforced: false` and `would_be` set. ALLOW stays
ALLOW. Quarantine still holds, because it is an explicit operator action.
Every attempt is recorded on the audit chain and in the registry exactly as it
would be under enforcement, so the evidence you review later is real evidence.

## 2. Understand

**Suggested rules.** `GET /v1/rules/suggested?project=...` drafts a rule per
agent from what it actually did: reads proposed as ALLOW, writes as ASK FIRST,
deletes as NEVER, and anything an enabled rule already covers left out. Each
draft carries the counts it was built from. Review it, narrow the resource
patterns, and save it. See [rules.md](../rules.md#where-rules-come-from).

**Drift.** For each agent, `GET /v1/agents/{id}/drift` compares three sets:

```text
DECLARED   what the identity claims it can do     (allowed_tools)
GRANTED    what its authority actually permits     (lease actions)
OBSERVED   what it asked for                       (requested_authority)
```

```text
TASK      "Summarise customer support tickets"
EXPECTED  tickets.read
OBSERVED  tickets.read
          customer.export        UNDECLARED
          email.send             UNDECLARED
```

`undeclared` (observed but not in the identity manifest) and `ungranted`
(observed but nothing permits it) are the two lists that decide whether an
agent is doing what it was built to do.

## 3. Govern

```bash
curl -X PATCH http://127.0.0.1:8000/v1/projects/prj_… \
  -H 'Content-Type: application/json' -b "ap_session=$COOKIE" \
  -d '{"mode": "govern"}'
```

Or **Settings → Mode** in the console.

Now the real decision comes back - `deny` as 403, `approval_required` as 202 -
with `enforced: false` and `advisory: true`. Nothing is stopped. This is where
you find the rule that was too narrow, before it costs anyone a work session.

Two things do not happen in govern mode: no approval request is opened, and no
use limit is consumed.

## 4. Enforce

Switch when every agent in the project is covered by a rule you have reviewed,
and the govern-mode denials you still see are the ones you want.

```bash
curl -X PATCH http://127.0.0.1:8000/v1/projects/prj_… \
  -H 'Content-Type: application/json' -b "ap_session=$COOKIE" \
  -d '{"mode": "enforce"}'
```

What "enforced" means still depends on the connector. `binding: true` in the
response says the decision actually stopped something; on an advisory
integration it stays false and the action was only recorded. See
[connectors.md](../connectors.md).

## Quarantine

Mode is per project. Quarantine is per agent and absolute, in every mode:

```bash
curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
  "http://127.0.0.1:8000/v1/agents/support-bot/quarantine?tenant=prj_…" \
  -H 'Content-Type: application/json' -d '{"note": "exporting customer data"}'
```

Every action from that agent returns `quarantine` (HTTP 423) until it is
released with `DELETE` on the same path. A console session or a management key
works in place of the admin token.

## The advanced path

`GET /v1/agents/{id}/suggested-lease` returns an AuthorityLease document built
from the agent's observed actions and resources for its current task, for
deployments that issue leases directly instead of writing rules. Review it,
replace resource literals with the narrowest pattern that still covers the
task, add `protected_resources` and a `permitted_consequence`, then issue it
with `POST /v1/leases`. See [authorization.md](authorization.md).
