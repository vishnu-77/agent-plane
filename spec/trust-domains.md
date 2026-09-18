# Trust domains

Status: implemented (0.8). `agent_plane/identity/trust.py` defines the
model. `AuthorityLease.allowed_trust_domains` and `Actor.trust_domain`
(populated in `gateway/identity.py`/`gateway/context.py`) wire it into
`agent_plane/authority/evaluator.py`'s opt-in identity gate - see
spec/identity-assurance.md.

## Project (tenant) is not a trust domain

`tenant` (the existing `Project`/API-key isolation boundary — see
`agent_plane/accounts/models.py`, `AgentRow`'s primary key) stays exactly
what it is: a **billing and data-isolation boundary**. It is not renamed,
repurposed, or migrated by this work.

A **trust domain** is a different, orthogonal concept: a statement about
*how an identity was issued or verified*, independent of which project it
happens to be acting in.

```text
tenant / Project      → isolation & billing boundary
TrustDomain            → issuance/verification boundary
```

One tenant can have agents from several trust domains (e.g. a CI-issued
identity and a human-operator identity in the same project). One trust
domain's issuer could, in principle, span multiple tenants. Conflating the
two would make it impossible to express "this agent was issued in `dev` but
is now requesting a `prod` resource" — a distinction the roadmap this work
is scoped from calls out explicitly as something that should become
visible, not silently allowed.

## Shape

```python
class TrustDomain(BaseModel):
    id: str
    name: str
    issuer: str | None = None
    environment: str = "production"   # production | staging | development
```

## No migration required

Every existing actor gets a synthetic trust domain for free, with zero
operator configuration, via `agent_plane/identity/trust.py`'s
`default_trust_domain_id(tenant) -> f"tenant:{tenant}"` (added in PR-3,
used by PR-4's registry). This is a deliberate design choice: it means
`tenant` continues to fully determine behavior everywhere it does today,
and a deployment only sees a *different* value once an operator explicitly
registers a real trust domain and some later feature starts consuming it
instead of the default. PR-4 itself changes no observable behavior.

## Out of scope here

Cross-domain policy (e.g. requiring stronger assurance when an agent from
`acme.dev` acts against `acme.prod`) is not implemented by PR-1..7. This doc
only fixes the vocabulary so that work has a model to build on.
