# Configuration

## Policies

Four shipped rules in `policies/`:

- `finance-data-external-model-restriction.yaml` - deny confidential/regulated
  finance & legal data to external models; exception for `azure_openai_private`.
- `pii-redaction-required.yaml` - redact email / credit_card / phone / api_key.
- `token-quota.yaml` - cap `max_tokens`, enforce per-user rolling token quota.
- `sensitive-tool-approval.yaml` - require human approval for high-impact tools
  (`wire_transfer`, `delete_records`, `send_external_email`).

Edit/add YAML and restart (or `POST /admin/policies/reload`); the bundle version
changes and is stamped on every new audit event.

## Models (config-driven)

The model/provider catalog is YAML - onboard models without touching code. Edit
`config/models.yaml` (or point `MODELS_FILE` elsewhere); if neither is present the
built-in defaults in `agent_plane/routing/registry.py` apply. Shipped: `gpt-4.1`,
`gpt-4o-mini` (OpenAI), `claude-sonnet` (Anthropic), `azure-private-gpt4` (Azure),
with `gpt-4.1` falling back to `gpt-4o-mini` on upstream failure.

```yaml
models:
  - id: my-model
    provider: openai
    upstream_model: gpt-4.1
    tags: [openai, external]      # tags are what policies match on
    fallback: [gpt-4o-mini]
```

## Tools, knowledge, leases

Same config-driven, default-deny pattern as models - see
[EDGES.md](EDGES.md) for each edge's file and shape:

- `config/tools.yaml` (or `TOOLS_FILE`) - the tool broker's catalog.
- `config/knowledge.yaml` (or `KNOWLEDGE_FILE`) - RAG sources + access metadata.
- `config/leases.yaml` (or `LEASES_FILE`) - `AuthorityLease` grants; also issuable
  at runtime via `POST /v1/leases`.
- `config/capability-manifest.yaml` + `config/threat-model.yaml` - kept in sync by
  `agentplane authority check-freshness` (CI-enforced).

## `.env` / environment variables

All knobs live in [`.env.example`](.env.example) - copy it, set at least one
provider key, and go. Production checklist (secrets, `IDENTITY_MODE=delegation`,
`ADMIN_TOKEN`, CORS): [SECURITY.md](SECURITY.md).

## Vercel deployment

Connect this repository with the repository root as the Root Directory and
`main` as the Production Branch. `pyproject.toml` declares the FastAPI entrypoint
as `agent_plane.main:app`. `vercel.json` selects FastAPI and automatic dependency
installation from `pyproject.toml`, overriding a stale dashboard install command
such as `pip install -r requirements.txt`. No build command or output directory
is needed. Branch pushes produce previews; merging to `main` deploys production.

Configure Preview and Production environment variables separately:

Leave unused settings unset. Empty environment values are treated as unset and
use the application's defaults, including optional provider settings. Empty
signing secrets still fail the production startup checks.

- `ENVIRONMENT=production` enables the existing fail-closed startup checks.
- Set independent, strong `JWT_SECRET`, `AUDIT_SIGNING_KEY`, and `ADMIN_TOKEN`
  values in Vercel; never commit them or copy the development secrets.
- For a console demo using `STORAGE_BACKEND=local`, set
  `SQLITE_PATH=/tmp/agent-plane-audit.db`. The deployed source directory is
  read-only; `/tmp` is writable but ephemeral and local to each instance.
- Provider calls require the corresponding provider keys. The shipped mock
  tools can be exercised without provider credentials.

The local-storage profile is for demonstrations: audit and usage history can
disappear on cold starts, and instances do not share them. Leases, their use
counters, and runtime revocations are also in memory. Postgres/Redis can persist
audit, usage, and cache/quota data (install the corresponding optional extras),
but do not make lease state shared or durable. Do not use this serverless demo
for enforcement that depends on durable revocation or global lease-use limits.

The in-memory lease constraint is not specific to serverless — it applies to
any multi-worker deployment. See
[SECURITY.md § Known limitations](SECURITY.md#known-limitations-read-before-relying-on-it).

Check the preview before merging:

```bash
vercel inspect <deployment-url> --logs
vercel curl /healthz --deployment <deployment-url>
vercel curl /readyz --deployment <deployment-url>
vercel curl /console --deployment <deployment-url>
```

Both probes must return HTTP 200 (`ok` and `ready`); `/` must redirect to
`/console`, and `/console` must return HTML. `/docs` serves the API explorer.
Unauthenticated authorization and audit requests must remain rejected. Repeat
these checks on the production deployment after merging; a successful build
alone does not prove that application startup or database access works.
