# Security Policy

## Reporting a vulnerability

Please report security issues privately - do **not** open a public issue.

- Email: **vishnu7stanite@gmail.com** (subject: `agent-plane security`)
- Or use GitHub's private "Report a vulnerability" advisory flow.

We aim to acknowledge within 3 business days and to provide a remediation
timeline after triage.

## Production hardening checklist

`agent-plane` is secure-by-default in `ENVIRONMENT=production` (it refuses to
start with default secrets). Before exposing it:

- [ ] Set a strong `JWT_SECRET` and `AUDIT_SIGNING_KEY` (≥ 32 bytes).
- [ ] Use `IDENTITY_MODE=delegation` with an Ed25519 `DELEGATION_PUBLIC_KEY`;
      keep the private key with the issuer only.
- [ ] Set a strong `ADMIN_TOKEN` (or leave the admin API disabled).
- [ ] Put the service behind TLS and your own network controls.
- [ ] Restrict `CORS_ORIGINS` to known front-ends.
- [ ] Review `policies/*.yaml` - a missing/empty policy dir falls back to
      bundled defaults; if every file present still resolves to zero policies,
      the app now refuses to start in production (warns and runs allow-all
      outside production).
- [ ] Treat the audit log as evidence: ship it to durable, append-only storage.

## Built-in abuse protection

- **Request-size cap** - bodies larger than `MAX_REQUEST_BYTES` (default 1 MB) are
  rejected with 413 (Content-Length check).
- **Rate limiting** - `RATE_LIMIT_PER_MINUTE` per client (default 600; 0 disables),
  keyed by IP. Behind a proxy, set `TRUST_FORWARDED_FOR=true` only if the proxy is
  trusted, so `X-Forwarded-For` is honored.
- **Audit endpoint** - `GET /v1/audit` is operator-only (admin token); disabled
  entirely unless `ADMIN_TOKEN` is set.
- **Audit chain integrity** - chain appends are serialized (an in-process lock plus
  a Postgres transaction-scoped advisory lock), and `event_hash` is unique, so the
  hash chain cannot fork or accept a duplicate link even under concurrent writers.

## Known limitations (read before relying on it)

**This is the single authoritative list.** Other documents link here rather
than keeping their own copy, so there is one place to check and one place to
update.

### The decision boundary

- **`/v1/authorize` enforces nothing.** It returns a decision; the caller
  executes. An agent that never calls it, or ignores a deny, is not
  constrained. Task authority governs an orchestrator you trust to ask — it is
  not a sandbox and does not contain a compromised or prompt-injected agent
  holding its own credentials. The tool broker and model proxy *are* binding,
  because the credential lives server-side.
- **`impact` is caller-declared.** `maximum_impact` gates a value supplied by
  the party being governed, and an omitted `impact` currently defaults to the
  permissive `reversible`. There is no server-side action→impact registry to
  cross-check against yet. It stops an honest agent, not a lying one.

### Identity and tenancy

- **`jwt_claims` identity mode trusts tokens as-is** and tokens may lack
  expiry. In this mode the agent asserts its own `agent_id`, `tenant`,
  `allowed_tools` and `clearance` — so **tenant isolation is only real under
  `IDENTITY_MODE=delegation`**. Use delegation in production (the server warns
  otherwise).
- **The admin plane is not tenant-scoped.** One `ADMIN_TOKEN` covers every
  tenant's leases, and `GET /v1/audit` is deliberately cross-tenant.

### Durability

- **Leases, use counters and runtime revocations are in-process memory.** They
  do not survive a restart and are not shared between workers or replicas: two
  workers means a `max_uses: 5` cap is effectively 10, and a revocation applies
  only to the process that received it. Run a single worker where those
  guarantees matter, until this moves to the database.
- **Serverless deployment voids the above entirely**, plus audit durability —
  ephemeral per-instance storage means the hash chain forks per instance and
  vanishes on cold start. The serverless profile is for demonstrations only.

### Audit

- **HMAC audit signing** is tamper-evident against parties without the key; an
  insider holding the key *and* database write access could rewrite the chain.
  Ed25519 (asymmetric) signing is the planned upgrade.
- **Truncation is undetectable.** `verify_chain()` proves the internal
  consistency of the rows it is given. Deleting a *middle* entry orphans the
  next `prev_hash` and is caught; deleting *trailing* entries leaves nothing
  pointing at them and is not. Ship the log to external append-only storage and
  checkpoint the chain head if you need truncation resistance. This gap has a
  standing regression test (`tests/test_audit_signing.py`).
- Rotating `AUDIT_SIGNING_KEY` invalidates verification of the prior chain.

### Data handling

- **PII/secret redaction is best-effort** (regex DLP), not a guaranteed
  boundary. On the tool broker edge it mutates arguments actually sent to the
  tool, so a false positive can corrupt a call.
- The tool broker executes **operator-configured** endpoints; validate any tool
  you add.

CI runs `pip-audit` to surface dependency CVEs.

## Scope

In scope: the control plane (policy decision, identity verification, audit
integrity, the broker). Out of scope: the upstream model providers themselves
and any tools you connect.
