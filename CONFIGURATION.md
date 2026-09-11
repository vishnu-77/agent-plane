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
- `config/leases.yaml` (or `LEASES_FILE`) - `AuthorityLease` grants seeded on
  first start; also issuable at runtime via `POST /v1/leases`. Stored copies win
  over the YAML on restart, so a revocation or shrink is never undone by a
  redeploy.
- `config/lease-templates.yaml` (or `LEASE_TEMPLATES_FILE`) - named lease shapes
  for `POST /v1/leases/from-template`; variables fill `{placeholders}` in
  resources and may not contain globs or traversal.
- `APPROVAL_TTL_SECONDS`, `APPROVAL_WEBHOOK_URL` - approval-request lifetime and
  the signed webhook for `approval.*` events.
- `AUTHORITY_STORE` (`sql` default, `memory`) - where leases, use counters,
  approvals, and the MCP request ledger live.
- `LOG_FORMAT` (`text`/`json`), `METRICS_ENABLED` - observability.
- `config/capability-manifest.yaml` + `config/threat-model.yaml` - kept in sync by
  `agentplane authority check-freshness` (CI-enforced).

## `.env` / environment variables

All knobs live in [`.env.example`](.env.example) - copy it, set at least one
provider key, and go. Production checklist (secrets, `IDENTITY_MODE=delegation`,
`ADMIN_TOKEN`, CORS): [SECURITY.md](SECURITY.md).
