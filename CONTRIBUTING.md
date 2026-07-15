# Contributing to agent-plane

Thanks for your interest! agent-plane is MIT-licensed and open to contributions.

## Dev setup

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                                            # full suite, offline
```

## Ground rules

- **Keep the trust path deterministic.** Enforcement decisions must not depend on
  a model/classifier. Probabilistic detection belongs on the observability side,
  downstream of the decision.
- **Fail closed.** New code paths default to deny + log on uncertainty.
- **Add tests.** Every behavior change ships with a test; the suite must stay green.
- **Config over code.** Prefer YAML/env configuration to hard-coded values, so
  operators can adapt without forking.

## Project layout

```
agent_plane/        the package (gateway, policy, routing, audit, usage, ...)
agent_plane/defaults/   bundled default policies + config (agentplane init)
policies/  config/  dev copies used when running from the repo
tests/              pytest suite
```

## Pull requests

1. Branch from `main`.
2. `pytest` green; add/adjust tests.
3. Keep PRs focused; describe the behavior change and any config it adds.

## Reporting security issues

Please follow [SECURITY.md](SECURITY.md) — do not open public issues for vulnerabilities.
