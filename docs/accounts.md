# Accounts, projects, and API keys

## The hierarchy

```text
User            a person, email + password
└── Workspace   created with the first account; holds members with roles
    └── Project one governed environment: keys, integrations, rules, mode, activity
```

A user belongs to one or more workspaces through a **membership** with a role
of `owner`, `admin`, or `member`. A project belongs to exactly one workspace.
Every agent action is recorded against exactly one project.

Sign-up is controlled by `SIGNUP_MODE`:

| Value | Meaning |
| --- | --- |
| `first_user` (default) | the first account owns the workspace; sign-up closes after it |
| `open` | anyone may create an account |
| `closed` | nobody may |

`GET /v1/auth/state` reports `users`, `signup_open`, and `first_run` before
anyone is signed in, which is what the sign-in screen renders.

## Console sessions

A human authenticates with `POST /v1/auth/login` and receives a signed,
httponly cookie named `ap_session` (`SameSite=Lax`, `Secure` in production).
It is a `base64(payload).hmac` pair with an expiry inside the payload - there
is no server-side session table and nothing is kept in local storage. The TTL
is `SESSION_TTL_SECONDS`, 14 days by default.

The cookie identifies a person. It never authorizes an agent action.

Passwords are stretched with scrypt and only the derived hash is stored.

## Projects

```bash
curl -X POST http://127.0.0.1:8000/v1/projects \
  -H 'Content-Type: application/json' -b "ap_session=$COOKIE" \
  -d '{"name": "checkout-api", "mode": "observe"}'
```

A project created through onboarding starts in `observe` whatever the
deployment default is. Fields:

| Field | Meaning |
| --- | --- |
| `mode` | `observe`, `govern`, or `enforce` - see [modes.md](modes.md) |
| `collection` | which fields are recorded; metadata on, content off |
| `demo` | true only for the hosted demo project, which cannot be deleted |

`PATCH /v1/projects/{id}` changes the mode or the collection policy;
`GET /v1/projects/{id}` also returns `collection_fields`, the default and
optional field lists the Settings screen renders.

### Data collection

Recorded by default: agent identity, integration identity, session metadata,
task metadata, action name, resource identifier, decision result, authority
metadata, consequence metadata.

Off unless a human turns it on, per project: `prompt_content`,
`tool_arguments`, `tool_output`, `file_content`, `model_messages`. The filter
is applied in the runtime **before** anything is stored, not in the console.

## API keys

A key belongs to one project and carries a prefix that says what it is for:

| Prefix | Environment | Default scopes | Used by |
| --- | --- | --- | --- |
| `ap_live_` | `live` | `ingest`, `authorize` | connectors and SDKs in normal use |
| `ap_test_` | `test` | `ingest`, `authorize` | the same, against a project you are experimenting in |
| `ap_mgmt_` | `mgmt` | `manage` | automation that reads a project's activity |

The secret is 32 url-safe characters after the prefix. It is shown **exactly
once**, at creation; the server stores only `HMAC-SHA256(key, API_KEY_SECRET_VALUE)`
plus the prefix and last four characters, so a key can be recognised and
displayed as `ap_live_••••••••••••x9fQ` but never recovered.

Create, rotate, and revoke from **Integrations → API keys** in the console,
or:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/v1/api-keys?project=` | list, masked, with `status` (`active`, `expired`, `revoked`) |
| POST | `/v1/api-keys` | `{project, name, environment?, expires_in_days?}`; the response's `secret` is the only copy |
| POST | `/v1/api-keys/{id}/rotate` | issues a replacement and revokes the old one in one step |
| DELETE | `/v1/api-keys/{id}` | revoke immediately |

These four routes, and every `/v1/projects*` route, require a console session
cookie. There is no API-key path to minting API keys.

### How a key is presented

Runtime routes (`/v1/events/action`, `/v1/authorize`, `/v1/sessions`,
`/v1/tasks`, `/v1/auth/exchange`) accept the key as either
`Authorization: Bearer ap_live_...` or `X-Api-Key: ap_live_...`. The project
is resolved from the key; the agent, session, and host come from the request
body or from `X-Agent-Id`, `X-Session-Id`, `X-Agent-Host`, `X-Integration`.

Read routes scoped to a project (`/v1/rules`, `/v1/integrations`,
`/v1/agents`, `/v1/decisions`, `/v1/system`, …) accept a **management** key in
the `X-Admin-Token` header. A management key is scoped to its own project and
nothing else.

Scopes are enforced: a `mgmt` key presented to `/v1/events/action` is refused
with `403 this API key lacks the 'ingest' scope`, and a `live` key presented
as a management credential is refused with `401 Invalid management key`.

### Rotation

`POST /v1/api-keys/{id}/rotate` returns the new plaintext and the id of the
key it revoked. Update the connector (`agentplane connect <target> --key
<new>` rewrites the stored credential) before the old key stops being used;
both are never valid at once, so plan a short window.

Changing `API_KEY_SECRET_VALUE` invalidates every issued key and signs out every
session. `API_KEY_SECRET_VALUE` defaults to `JWT_SECRET`; set it explicitly in production.

## The deployment's break-glass token

`ADMIN_TOKEN` still exists. It is unscoped - it reads and writes every tenant
- and it is an implementation detail of self-hosting: policy reload,
credential revocation, direct lease issuance. Nothing in onboarding asks for
it, and if it is unset those routes return 404. See
[deployment.md](deployment.md).

## Exchange: what a connector does at start-up

```bash
curl -X POST http://127.0.0.1:8000/v1/auth/exchange \
  -H "Authorization: Bearer $AGENTPLANE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"integration": "claude-code", "host": "laptop"}'
```

The response names the project and its mode, registers (or refreshes) the
integration, echoes the collection policy the connector should respect, and
gives the two endpoints it should use:

```json
{"project": {"id": "prj_…", "name": "checkout-api", "mode": "observe"},
 "integration": {"id": "int_…", "kind": "claude-code",
                 "observation": "full", "enforcement": "partial"},
 "agent": "claude-code", "session": "ses_…",
 "collect": {"prompt_content": false, "...": true},
 "report_to": "/v1/events/action", "authorize_at": "/v1/authorize"}
```

No token is minted. The API key remains the credential.


## Single sign-on (optional)

The console can sign people in with your identity provider instead of a
password. It is off until three settings are present, and it changes nothing
else about the system.

```bash
OIDC_ISSUER=https://acme.okta.com        # or Auth0, Google, Entra, Keycloak
OIDC_CLIENT_ID=…
OIDC_CLIENT_SECRET=…
```

Register `https://<your-host>/v1/auth/oidc/callback` with the provider. There
is no provider-specific configuration beyond that: the issuer's discovery
document supplies the endpoints.

**SSO is the portal door, not the plane's.** It issues exactly the session
cookie a password login issues. Agents keep authenticating with a Project API
Key, which means a provider outage cannot stop an agent being governed, and a
stolen browser session can never act as an agent. The runtime never reads a
session cookie at all.

Two more settings, both optional:

| | |
| --- | --- |
| `OIDC_ALLOWED_DOMAINS` | comma-separated email domains allowed to sign in |
| `OIDC_ONLY` | stop offering the password form; ignored while SSO is unconfigured, so a typo in the issuer cannot lock everyone out |
| `OIDC_REDIRECT_URL` | only needed behind a proxy, where the request's host is not the public one |

Accounts are linked by the provider's subject, not the email address. The
first sign-in adopts a matching account, or creates one if your sign-up mode
allows it, and records the subject from then on. An email that already belongs
to a different subject is refused rather than adopted, so a reassigned address
cannot inherit someone's account. An unverified email is refused outright.

What SSO does not do: no group or role claims are read. It decides who may
sign in, never what they may do. Project access is still the account model,
and deployment-wide operations still need the instance owner, a management
key, or `ADMIN_TOKEN`.
