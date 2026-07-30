# Security Policy

## Reporting a vulnerability

Please report security issues privately - do **not** open a public issue.

- Email: **team@togro.co** (subject: `agent-plane security`)
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
- [ ] Review `policies/*.yaml` - an empty policy dir means allow-all (the app
      warns, and falls back to bundled defaults).
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

- **HMAC audit signing** is tamper-evident against parties without the key; an insider
  holding the key *and* database write access could rewrite the chain. Ed25519
  (asymmetric) signing is the planned upgrade.
- **PII redaction is best-effort** (regex DLP), not a guaranteed boundary.
- **`jwt_claims` identity mode trusts tokens as-is** and tokens may lack expiry. Use
  `IDENTITY_MODE=delegation` in production (the server warns otherwise).
- The tool broker executes **operator-configured** endpoints; validate any tool you add.

CI runs `pip-audit` to surface dependency CVEs.

## Scope

In scope: the control plane (policy decision, identity verification, audit
integrity, the broker). Out of scope: the upstream model providers themselves
and any tools you connect.
