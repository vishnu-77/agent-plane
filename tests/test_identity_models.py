"""PrincipalIdentity, IdentityAssurance, TrustDomain - additive identity
package. Nothing in the request path is required to call this, but
Actor.assurance/trust_domain (populated in gateway/context.py and
gateway/identity.py) are real, load-bearing signals for the evaluator's
opt-in identity gate (see test_identity_evaluator_gate.py)."""
from agent_plane.identity.assurance import ASSURANCE_RANK, IdentityAssurance
from agent_plane.identity.models import PrincipalIdentity
from agent_plane.identity.resolver import resolve_principal
from agent_plane.identity.trust import TrustDomain, TrustDomainRegistry, default_trust_domain_id
from agent_plane.schemas.canonical import Actor


def test_assurance_ladder_values():
    assert [a.value for a in IdentityAssurance] == [
        "reported",
        "connector_authenticated",
        "delegated_verified",
        "workload_attested",
    ]


def test_assurance_rank_is_ordered():
    assert (ASSURANCE_RANK[IdentityAssurance.REPORTED]
            < ASSURANCE_RANK[IdentityAssurance.CONNECTOR_AUTHENTICATED]
            < ASSURANCE_RANK[IdentityAssurance.DELEGATED_VERIFIED]
            < ASSURANCE_RANK[IdentityAssurance.WORKLOAD_ATTESTED])


def test_principal_identity_defaults():
    p = PrincipalIdentity(principal_id="agt_1")
    assert p.assurance == IdentityAssurance.REPORTED
    assert p.principal_type == "agent"
    assert p.owner_principal is None
    assert p.workload_id is None
    assert p.authentication_method is None


def test_resolve_principal_without_api_key_is_reported():
    actor = Actor(user_id="u1", tenant="acme", agent_id="claude-code")
    principal = resolve_principal(actor)
    assert principal.assurance == IdentityAssurance.REPORTED
    assert principal.subject == "claude-code"
    assert principal.trust_domain == "tenant:acme"


def test_resolve_principal_with_api_key_is_connector_authenticated():
    actor = Actor(user_id="u1", tenant="acme", agent_id="claude-code")
    principal = resolve_principal(actor, api_key_id="key_123")
    assert principal.assurance == IdentityAssurance.CONNECTOR_AUTHENTICATED
    assert principal.issuer == "key_123"


def test_resolve_principal_prefers_actors_own_assurance():
    actor = Actor(user_id="u1", tenant="acme", agent_id="claude-code",
                 assurance=IdentityAssurance.DELEGATED_VERIFIED)
    principal = resolve_principal(actor, api_key_id="key_123")
    assert principal.assurance == IdentityAssurance.DELEGATED_VERIFIED


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
    test_assurance_rank_is_ordered()
    test_principal_identity_defaults()
    test_resolve_principal_without_api_key_is_reported()
    test_resolve_principal_with_api_key_is_connector_authenticated()
    test_resolve_principal_prefers_actors_own_assurance()
    test_default_trust_domain_id_is_deterministic_per_tenant()
    test_trust_domain_registry_round_trips()
    print("ok")
