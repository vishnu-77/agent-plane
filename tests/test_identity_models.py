"""PR-3/PR-4: PrincipalIdentity, IdentityAssurance, TrustDomain - additive,
unwired identity package. Nothing in the request path calls into this yet."""
from agent_plane.identity import (
    IdentityAssurance,
    PrincipalIdentity,
    TrustDomain,
    TrustDomainRegistry,
    default_trust_domain_id,
    resolve_principal,
)
from agent_plane.schemas.canonical import Actor


def test_assurance_ladder_values():
    assert [a.value for a in IdentityAssurance] == [
        "self_asserted",
        "api_key_bound",
        "runtime_credential",
        "verified_delegation",
    ]


def test_principal_identity_defaults_to_self_asserted():
    p = PrincipalIdentity(principal_id="agt_1")
    assert p.assurance == IdentityAssurance.SELF_ASSERTED


def test_resolve_principal_without_api_key_is_self_asserted():
    actor = Actor(user_id="u1", tenant="acme", agent_id="claude-code")
    principal = resolve_principal(actor)
    assert principal.assurance == IdentityAssurance.SELF_ASSERTED
    assert principal.subject == "claude-code"
    assert principal.trust_domain == "tenant:acme"


def test_resolve_principal_with_api_key_is_api_key_bound():
    actor = Actor(user_id="u1", tenant="acme", agent_id="claude-code")
    principal = resolve_principal(actor, api_key_id="key_123")
    assert principal.assurance == IdentityAssurance.API_KEY_BOUND
    assert principal.issuer == "key_123"


def test_default_trust_domain_id_is_deterministic_per_tenant():
    assert default_trust_domain_id("acme") == default_trust_domain_id("acme")
    assert default_trust_domain_id("acme") != default_trust_domain_id("other")


def test_trust_domain_registry_round_trips():
    registry = TrustDomainRegistry()
    assert registry.get("acme.dev") is None
    domain = TrustDomain(id="acme.dev", name="Acme Dev", environment="development")
    registry.upsert(domain)
    assert registry.get("acme.dev") == domain


if __name__ == "__main__":
    test_assurance_ladder_values()
    test_principal_identity_defaults_to_self_asserted()
    test_resolve_principal_without_api_key_is_self_asserted()
    test_resolve_principal_with_api_key_is_api_key_bound()
    test_default_trust_domain_id_is_deterministic_per_tenant()
    test_trust_domain_registry_round_trips()
    print("ok")
