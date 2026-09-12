# Hosted demo

The fastest way to see a decision without connecting an agent. The console has
a **LIVE / DEMO** switch; in DEMO the page shows a persistent marker:

```text
DEMO ENVIRONMENT · NO EXTERNAL SIDE EFFECTS
```

Nothing in the demo is browser-only data. Every scenario runs through the real
authority engine, the real registry, and the real signed audit chain. Only the
targets are simulated.

```text
Console
   ├── LIVE  ──► your projects, via your session cookie
   └── DEMO  ──► the isolated demo project, via the demo viewer token
                     │
                     ▼
              real authority engine
                     │
                     ▼
              simulated resources (staging, production, GitHub)
```

The demo runs in its own project, `prj_demo`, owned by nobody. It is created
on first start, runs in `enforce` mode, and cannot be deleted.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/demo/scenarios` | the scenarios, their prompts, authority, and steps; also returns the demo viewer token |
| POST | `/demo/reset` | revoke demo leases, clear demo agents and tasks, reset simulated targets |
| POST | `/demo/scenarios/{name}/run` | run every step, or `{"steps": [2]}` for one; `run_id` keeps a multi-call run coherent |
| GET | `/demo/targets` | simulated target state and execution log |

Enabled with `DEMO_ENABLED=true` (default). The demo viewer token
(`DEMO_TOKEN`, default `demo`) is sent as `X-Demo-Token` and reads only the
demo project through the registry and decision endpoints - always read-only,
even alongside a signed-in session. Disable the demo on production deployments
that are not the hosted demo.

## Scenarios

### Staging incident (`staging-incident`)

> Investigate why checkout is failing in staging. Restart checkout if
> required. Do not touch production.

Authority: `logs.read metrics.read deployment.read deployment.restart` on
`staging/checkout`, production protected, permitted consequence limited to
staging and non-customer-facing effects.

```text
logs.read            staging/checkout      ALLOW
metrics.read         staging/checkout      ALLOW
deployment.restart   staging/checkout      ALLOW      executed against the simulated target
deployment.restart   production/checkout   DENY       RESOURCE_OUTSIDE_DELEGATED_SCOPE
```

### GitHub maintenance (`github-maintenance`)

> Remove stale branches from this repository. Never modify main.

Authority: `repository.read branch.list branch.delete` on
`github://demo/agent-plane/*`, `main` protected, at most five deletions, blast
radius 1.

```text
branch.list                                      ALLOW
branch.delete   branches/stale-feature           ALLOW   low downstream consequence
branch.delete   branches/main                    DENY    default branch removal -> CI/CD and deployments
```

### Multi-agent delegation (`delegation`)

> Investigate the checkout latency regression. Report findings; do not change
> anything.

`incident-agent` holds read-only authority and delegates `metrics.read` to
`metrics-agent`. The child then proposes `deployment.restart`.

```text
metrics.read         production/checkout   ALLOW   (parent)
metrics.read         production/checkout   ALLOW   (child, attenuated lease)
deployment.restart   production/checkout   DENY    No authority lineage permits deployment.restart.
                                                   human -> incident-agent -> metrics-agent
```

## Running it locally

```bash
agentplane serve --host 127.0.0.1
# open http://127.0.0.1:8000/console, switch to DEMO, press "Staging incident"
```

Each step's decision and trace is real and can be opened in the evidence
drawer. The demo uses directly issued leases rather than project rules,
because it also demonstrates delegation and lineage - see
[integration/authorization.md](integration/authorization.md).
