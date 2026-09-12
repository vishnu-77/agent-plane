# Observe → Enforce

Nobody can write a complete authority model for agents they have not
watched. So adoption starts by watching.

```text
OBSERVE
  ↓ discover agents            every governed call creates the agent record
  ↓ discover tasks             POST /v1/tasks records the prompt or event that raised it
  ↓ observe actions            requested vs exercised vs denied, per action
  ↓ observe resources          what was actually touched, with its consequence profile
  ↓ infer requested capability declared (identity) vs observed drift
  ↓ suggested authority        GET /v1/agents/{id}/suggested-lease
  ↓ review                     tighten scope, add protected resources, bound consequence
ENFORCE
```

## Turn it on

Globally with `ENFORCEMENT_MODE=observe`, or per tenant:

```bash
curl -X PUT -H "X-Admin-Token: $ADMIN_TOKEN" -d '{"mode":"observe","tenant":"acme"}' http://localhost:8000/admin/mode
```

The console's Runtime screen has the same switch, and the mode lamp in the
top bar shows which mode the current tenant is in.

## What observe mode does

Nothing is blocked. A decision that would have been DENY or APPROVAL comes
back as **SIMULATE** (HTTP 200) with `enforced: false` and `would_be` set.
ALLOW stays ALLOW. QUARANTINE still holds, because it is an explicit
operator action. Every attempt is recorded on the audit chain and in the
registry exactly as it would be under enforcement, so the evidence you review
later is the real evidence.

The SDKs expose this honestly: `decision.enforced` is false, `decision.proceed`
is true, `decision.allowed` stays false. The adapters run the tool on
`proceed`, so an executor in observe mode behaves as if agent-plane were not
there, while everything it does is being learned.

## Drift

For each agent, `GET /v1/agents/{id}/drift` compares three sets:

```text
DECLARED   what the identity claims it can do     (allowed_tools)
GRANTED    what its leases actually permit         (actions)
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
(observed but no lease permits it) are the two lists that decide whether an
agent is doing what it was built to do.

## Suggested authority

`GET /v1/agents/{id}/suggested-lease` returns a lease document built from
the agent's observed actions and resources for its current task. It is a
starting point: review it, replace resource literals with the narrowest
pattern that still covers the task, add `protected_resources` and a
`permitted_consequence`, then issue it with `POST /v1/leases`. Switch the
tenant to enforce when every agent it runs has a reviewed lease.

## Quarantine

Observe mode is per tenant. Quarantine is per agent and absolute:

```bash
curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" "http://localhost:8000/v1/agents/support-bot/quarantine?tenant=acme" -d '{"note":"exporting customer data"}'
```

Every action from that agent returns `quarantine` (HTTP 423) until it is
released, in either mode.
