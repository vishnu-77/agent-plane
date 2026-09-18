# Principals

Status: terminology freeze for the identity-first work (PR-1 of the
identity/authority first sprint). No new enforcement behavior.

## What this is

A **principal** answers *who or what is acting* — not what it can do
(that's [capability](capability-authority.md)) and not what it may do for a
task (that's `AuthorityLease`, see [authority-lease.md](authority-lease.md)).

`PrincipalIdentity` (introduced in code by PR-3, `agent_plane/identity/models.py`)
is the resolved-identity record for this concept:

```python
class PrincipalIdentity(BaseModel):
    principal_id: str
    issuer: str | None
    subject: str | None
    trust_domain: str | None
    assurance: IdentityAssurance
```

## Do not conflate with `Identity` in `accounts/oidc.py`

`agent_plane/accounts/oidc.py` already defines a class named `Identity`. That
is the **console login identity** for a human operator signing into the
Agent Plane dashboard via OIDC — unrelated to agent/principal identity. A
`PrincipalIdentity` describes an *agent* (or the API key/session acting on
its behalf); the OIDC `Identity` describes a *human console user*. Do not
merge these models or route console auth through the identity package.

## Relationship to `Actor`

`Actor` (`agent_plane/schemas/canonical.py`) remains the execution-oriented
projection used throughout the request path today — the thing the evaluator,
enforcement service, and policy engine already consume. `PrincipalIdentity`
sits underneath it, not in place of it:

```text
PrincipalIdentity  (who/how verified)
      │
      ▼
    Actor           (execution-oriented projection: tenant, allowed_tools/capabilities, clearance)
      │
      ▼
    Task
      │
      ▼
AuthorityLease
```

`agent_plane/identity/resolver.py`'s `resolve_principal(actor, ...)` derives
a `PrincipalIdentity` *from* an `Actor` — it does not replace `Actor`, and
nothing in the request path is required to call it yet (PR-3 ships it
unwired; later work decides where to call it).

## Relationship to `authority-provenance.md`'s `AgentIdentity`

[authority-provenance.md](authority-provenance.md) (a pre-existing, still
unimplemented design) already proposes an `AgentIdentity` record with a
"verified principal binding" field, as part of a broader event-provenance
model. `PrincipalIdentity` is the narrower, now-implemented piece of that
idea: it exists to answer *who is this principal and how strongly do we know
it*, decoupled from the rest of `authority-provenance.md`'s event/lineage
machinery (`AgentRun`, `GrantVersion`, `Admission`, etc., which remain
proposed, not built). When `authority-provenance.md`'s `AgentIdentity` is
eventually implemented, it should incorporate `PrincipalIdentity` rather than
define a second, competing identity record.
