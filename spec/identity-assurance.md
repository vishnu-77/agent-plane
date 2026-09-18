# Identity assurance

Status: implemented (0.8). `agent_plane/identity/assurance.py` defines the
enum and rank table; `Actor.assurance` (`agent_plane/schemas/canonical.py`)
carries it on every resolved identity; `AuthorityLease.min_assurance` /
`require_established_identity` / `allowed_trust_domains`
(`agent_plane/authority/lease.py`) let a task opt into requiring it, checked
in `agent_plane/authority/evaluator.py` immediately after a task's active
leases are established, before the NEVER-list and resource checks. **Opt-in
per lease** - a lease that doesn't set these fields can never produce
`IDENTITY_NOT_ESTABLISHED` / `IDENTITY_ASSURANCE_INSUFFICIENT` /
`TRUST_DOMAIN_NOT_ALLOWED`, so no existing lease's behavior changed.

## Why a ladder, not a score

There are several distinct strengths behind what used to be a single
"verified" label. Trust stays structural (a named, ordered set of levels), not
a numeric "trust score" - a level is either met or it isn't, and the reason is
always inspectable.

## The ladder

```python
class IdentityAssurance(str, Enum):
    REPORTED = "reported"
    CONNECTOR_AUTHENTICATED = "connector_authenticated"
    DELEGATED_VERIFIED = "delegated_verified"
    WORKLOAD_ATTESTED = "workload_attested"
```

| Level | Meaning | Where it's actually set today |
| --- | --- | --- |
| `reported` | The caller's own claim (an `X-Agent-Id` header, a body field, an unsigned dev JWT) is trusted with no independent check beyond the Project API Key or JWT secret itself. | `gateway/context.py`'s Project-API-Key branch; `gateway/identity.py`'s `_resolve_claims` (`identity_mode=jwt_claims`). |
| `connector_authenticated` | The caller presented a runtime credential minted by `POST /v1/auth/exchange` (`agent_plane/gateway/runtime_credential.py`) - proof it went through the exchange flow that registers the connector/integration, not just a bare per-request self-assertion. | `gateway/context.py`'s `_try_runtime_credential` branch. |
| `delegated_verified` | A cryptographically signed, verified scope - `identity_mode=delegation` / A2A child tokens. | `gateway/identity.py`'s `_resolve_delegation`. |
| `workload_attested` | Reserved. No code path in this repository produces it yet - it exists so a future cloud/orchestrator workload-attestation integration (SPIFFE, cloud instance identity, TPM) has a rung to land on without a schema change. | Not yet implemented. |

`ASSURANCE_RANK` orders these low-to-high for `min_assurance` comparisons; an
unrecognized or missing level ranks below `reported` (fail closed), the same
convention `authority/lease.py`'s `IMPACT_RANK` already uses.

## Opting a task in

```json
POST /v1/leases
{
  "id": "lease-prod-deploy", "task": "deploy-checkout", "subject": "release-bot",
  "resources": ["prod/*"], "actions": ["deployment.restart"],
  "min_assurance": "delegated_verified"
}
```

An actor below that floor gets `IDENTITY_ASSURANCE_INSUFFICIENT`, not a
generic deny - the reason is always inspectable via `GET /v1/decisions/{id}`.

Delegation can only tighten these fields, never loosen them - see
`lease_attenuation_errors` in `agent_plane/authority/lease.py`: a child lease
may not drop `require_established_identity`, lower `min_assurance` below its
parent's floor, or widen `allowed_trust_domains` past its parent's set.

## Relationship to `authority-provenance.md`'s evidence-quality categories

[authority-provenance.md § 3](authority-provenance.md) defines a
similar-looking but distinct axis - **evidence quality** for an *event*
(`gateway_observed`, `issuer_recorded`, `orchestrator_reported`,
`client_asserted`, `model_inferred`, `legacy_partial`). Do not merge these
two enums:

- `IdentityAssurance` answers *how strongly do we know who this principal
  is*, evaluated once per resolved identity.
- Evidence quality answers *how strongly do we trust a specific recorded
  event/claim* (e.g. a capability observation, a delegation transaction),
  evaluated per record.

## Not yet done

No UI surfaces `Actor.assurance` or a lease's identity requirements anywhere
- it's fully backend/API today (`AuthorityLease`, `AuthorityDecision.reason`,
and `PrincipalIdentity.assurance` in `agent_plane/identity/models.py`, which
remains a read/reporting model, not itself consulted by the evaluator).
