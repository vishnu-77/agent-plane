# Identity assurance

Status: terminology freeze for the identity-first work (PR-1). Implemented
in code by PR-3 (`agent_plane/identity/assurance.py`) as an unwired enum —
nothing in the request path derives or checks assurance yet.

## Why a ladder, not a score

Today the system collapses every agent into "verified" or not. In practice
there are several distinct strengths behind that label — a self-reported
`X-Agent-Id` header is not the same claim as a cryptographically verified
delegation. Trust stays structural (a named, ordered set of levels), not a
numeric "trust score" — a level is either met or it isn't, and the reason is
always inspectable.

## The ladder

```python
class IdentityAssurance(str, Enum):
    SELF_ASSERTED = "self_asserted"
    API_KEY_BOUND = "api_key_bound"
    RUNTIME_CREDENTIAL = "runtime_credential"
    VERIFIED_DELEGATION = "verified_delegation"
```

| Level | Meaning | Where it comes from today |
| --- | --- | --- |
| `self_asserted` | The caller's own claim (an `X-Agent-Id` header, a body field) is trusted with no independent check. | The default for every Project-API-Key request today — see `gateway/context.py:resolve_request`, the `presented`/API-key branch. |
| `api_key_bound` | Tied to a resolvable Project API Key, so at least the *project* is authenticated, even though agent/session identity within it is still self-asserted. | Same code path, once `resolve_principal()` (PR-3) is actually called with `api_key_id` set. |
| `runtime_credential` | Bound to a short-lived credential issued by `/v1/auth/exchange` (PR-5), carrying the identity claims the caller can no longer freely restate per request. | New in PR-5, opt-in, requires `DELEGATION_SIGNING_KEY` configured. |
| `verified_delegation` | A cryptographically signed, verified scope — today's existing `identity_mode=delegation` / A2A child-token flow (`gateway/a2a.py`, `gateway/identity.py`). | Already implemented, predates this work. |

## Relationship to `authority-provenance.md`'s evidence-quality categories

[authority-provenance.md § 3](authority-provenance.md) already defines a
similar-looking but distinct axis — **evidence quality** for an *event*
(`gateway_observed`, `issuer_recorded`, `orchestrator_reported`,
`client_asserted`, `model_inferred`, `legacy_partial`). Do not merge these
two enums:

- `IdentityAssurance` answers *how strongly do we know who this principal
  is*, evaluated once per resolved identity.
- Evidence quality answers *how strongly do we trust a specific recorded
  event/claim* (e.g. a capability observation, a delegation transaction),
  evaluated per record.

An identity with `runtime_credential` assurance can still produce an event
with `client_asserted` evidence quality (e.g. a self-reported capability
list) — the two are orthogonal, not the same ladder at different names.

## Non-goals for this PR

No UI copy is being changed here, and no existing "Verified Agent" display
is touched. This doc exists so that when the console UI is later updated to
stop collapsing these into one label (a later roadmap phase, not part of
PR-1..7), it has one agreed vocabulary to draw from.
