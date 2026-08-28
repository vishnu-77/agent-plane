# agent-plane - Enterprise Agentic AI Control Plane

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An open-source (MIT), **deterministic control plane for AI agents**: an
OpenAI-compatible gateway that turns every AI request - *and every tool call* - into a
**governed flow** instead of a raw `prompt → model → response`. Identity, configurable
policy, tool least-privilege, guardrails, routing, **usage metering**, and a
tamper-evident audit log are all applied at runtime. Drop-in (`base_url`),
config-driven, and packaged: `pip install agent-plane` → `agentplane serve`.

> **Capability ≠ Authority.** An agent may hold a real `github.delete_repository`
> credential (its *capability*) - the task at hand might only authorise
> `branch:list`/`branch:delete` on one repo (its *authority*). `POST /v1/authorize`
> decides that, per action, before it reaches the real system - see
> [EDGES.md](EDGES.md#task-authority-edge-agent--action) and
> [`spec/authority-lease.md`](spec/authority-lease.md).

```
POST /v1/chat/completions
  → Identity (JWT → Actor)
  → Normalize (OpenAI → CanonicalAIRequest; derived data classification + tools)
  → Policy Decision Point (YAML rules → Decision + obligations)
  → Tool least-privilege (enforce Actor.allowed_tools)
  → Token quota
  → Guardrails (PII/secret redaction)
  → Model routing (registry + fallback)
  → Response filtering (redaction + blocked tool calls)
  → Audit logging (hash-chained + HMAC-signed)
```

## Quick start (zero setup: SQLite + in-memory)

```bash
python -m venv .venv && . .venv/Scripts/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                                # set at least one provider key
agentplane serve --reload
```

The core install is minimal - the zero-setup path needs no native-heavy packages.
Optional extras (`[delegation]`, `[tokens]`, `[postgres]`, `[redis]`, `[server]`,
`[all]`) are listed in [`pyproject.toml`](pyproject.toml).

```bash
# Mint a dev JWT
python - <<'PY'
import jwt
print(jwt.encode(
    {"sub": "vishnu", "tenant": "default", "department": "support"},
    "dev-secret-change-me", algorithm="HS256"))
PY

# Call the gateway
TOKEN="<jwt from above>"
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"model":"gpt-4.1","messages":[{"role":"user","content":"hello"}]}'
```

The response includes an `x_control_plane` block with the `decision_id`,
`policy_version`, `rules_matched`, and any `redactions_applied`. More worked
examples per edge, including a policy-denial demo: [EDGES.md](EDGES.md).

## Endpoints

| Method | Path                    | Purpose                                  |
| ------ | ----------------------- | ---------------------------------------- |
| POST   | `/v1/chat/completions`  | Governed OpenAI-compatible completion    |
| POST   | `/v1/tools/invoke`      | Governed tool/MCP call (broker edge)     |
| POST   | `/v1/retrieve`          | Identity-aware RAG retrieval (auth edge) |
| POST   | `/v1/agents/delegate`   | Scoped agent-to-agent delegation (A2A)   |
| POST   | `/v1/authorize`         | Task-authority decision (capability ≠ authority) |
| POST   | `/v1/leases`            | Issue an `AuthorityLease` (**admin token only**) |
| GET    | `/v1/leases/{id}`       | Inspect a lease (**admin token only**)   |
| GET    | `/v1/usage`             | Per-tenant usage metering                |
| GET    | `/v1/models`            | Registered logical models                |
| GET    | `/v1/audit?limit=50`    | Recent audit events (**admin token only**) |
| GET    | `/healthz`              | Liveness + active policy bundle version  |
| GET    | `/readyz`               | Readiness (checks the audit store)       |

Full walkthroughs with curl for every edge, plus identity modes
(dev vs. verified delegation): **[EDGES.md](EDGES.md)**.

## View it in the browser

`agentplane serve`, then open:

- **`/console`** - operator dashboard: live status + policy version, all edges/routes, loaded
  policies, per-tenant usage, and the signed audit chain. Paste a bearer token (for usage)
  and an `X-Admin-Token` (for audit/policies) into the header fields. No build step, no JS deps.
- **`/docs`** - Swagger UI to call every endpoint interactively; **`/redoc`** - reference docs.

## Run with Docker

```bash
docker build -t agent-plane .
docker run -p 8000:8000 --env-file .env agent-plane   # runs `agentplane serve`, non-root, healthchecked
```

## Tests

```bash
pytest    # policy engine, every edge, full gateway flow - offline, no API keys needed
```

## More

- **[EDGES.md](EDGES.md)** - every edge's curl walkthrough, identity modes
- **[CONFIGURATION.md](CONFIGURATION.md)** - policies, models, tools, knowledge, leases, `.env`
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - design principles, control-plane/edge model, Postgres+Redis
- **[INTEGRATION.md](INTEGRATION.md)** - wiring this into an existing product (what's zero-code, what isn't)
- **[spec/authority-lease.md](spec/authority-lease.md)** - the `AuthorityLease` object, evaluation order, reason codes
- **[SECURITY.md](SECURITY.md)** - production hardening checklist, abuse protection, known limitations
- **[ROADMAP.md](ROADMAP.md)** - staged plan, what's shipped vs. planned

## License

MIT - see [LICENSE](LICENSE). Open source now; contributions welcome.
