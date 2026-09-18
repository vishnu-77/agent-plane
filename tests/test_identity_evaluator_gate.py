"""Phase 4: identity as an explicit evaluator prerequisite - opt-in per
lease via require_established_identity/min_assurance/allowed_trust_domains.
A lease that doesn't set these (every lease before 0.8) must behave exactly
as before; the gate only bites when a lease explicitly asks for it."""
from __future__ import annotations

from agent_plane.authority.evaluator import AuthorityReason, evaluate_authority
from agent_plane.authority.lease import AuthorityLease, lease_attenuation_errors
from agent_plane.authority.store import LeaseStore
from agent_plane.identity.assurance import IdentityAssurance
from agent_plane.schemas.canonical import Actor


def _actor(**overrides) -> Actor:
    base = dict(user_id="u1", tenant="default", agent_id="agt", allowed_tools=[])
    base.update(overrides)
    return Actor(**base)


def _lease(**overrides) -> AuthorityLease:
    base = dict(id="lease-1", task="task", subject="agt", resources=["r/*"], actions=["x.do"])
    base.update(overrides)
    return AuthorityLease(**base)


def _store(*leases) -> LeaseStore:
    store = LeaseStore()
    for lease in leases:
        store.add(lease)
    return store


def test_default_lease_ignores_missing_assurance_entirely():
    """No require_established_identity/min_assurance/allowed_trust_domains
    set -> an actor with assurance=None (never populated) is allowed exactly
    as before this change."""
    store = _store(_lease())
    actor = _actor(assurance=None, trust_domain=None)
    decision = evaluate_authority(store, actor, task="task", action="x.do", resource="r/1")
    assert decision.allowed


def test_require_established_identity_denies_unestablished_actor():
    store = _store(_lease(require_established_identity=True))
    actor = _actor(assurance=None)
    decision = evaluate_authority(store, actor, task="task", action="x.do", resource="r/1")
    assert decision.decision == "deny"
    assert decision.reason == AuthorityReason.IDENTITY_NOT_ESTABLISHED


def test_require_established_identity_allows_established_actor():
    store = _store(_lease(require_established_identity=True))
    actor = _actor(assurance=IdentityAssurance.REPORTED)
    decision = evaluate_authority(store, actor, task="task", action="x.do", resource="r/1")
    assert decision.allowed


def test_min_assurance_denies_below_floor():
    store = _store(_lease(min_assurance=IdentityAssurance.DELEGATED_VERIFIED.value))
    actor = _actor(assurance=IdentityAssurance.REPORTED)
    decision = evaluate_authority(store, actor, task="task", action="x.do", resource="r/1")
    assert decision.decision == "deny"
    assert decision.reason == AuthorityReason.IDENTITY_ASSURANCE_INSUFFICIENT


def test_min_assurance_allows_at_or_above_floor():
    store = _store(_lease(min_assurance=IdentityAssurance.CONNECTOR_AUTHENTICATED.value))
    actor = _actor(assurance=IdentityAssurance.DELEGATED_VERIFIED)
    decision = evaluate_authority(store, actor, task="task", action="x.do", resource="r/1")
    assert decision.allowed


def test_allowed_trust_domains_denies_outside_domain():
    store = _store(_lease(allowed_trust_domains=["tenant:acme.prod"]))
    actor = _actor(trust_domain="tenant:acme.dev", assurance=IdentityAssurance.REPORTED)
    decision = evaluate_authority(store, actor, task="task", action="x.do", resource="r/1")
    assert decision.decision == "deny"
    assert decision.reason == AuthorityReason.TRUST_DOMAIN_NOT_ALLOWED


def test_allowed_trust_domains_allows_matching_domain():
    store = _store(_lease(allowed_trust_domains=["tenant:acme.dev"]))
    actor = _actor(trust_domain="tenant:acme.dev", assurance=IdentityAssurance.REPORTED)
    decision = evaluate_authority(store, actor, task="task", action="x.do", resource="r/1")
    assert decision.allowed


def test_capability_gate_still_checked_first_for_backward_compatible_reason_ordering():
    """A lease that opts into identity requirements AND the actor also lacks
    capability coverage must still report the capability reason - the
    capability gate's position (before lease lookup) is unchanged."""
    store = _store(_lease(require_established_identity=True))
    actor = _actor(assurance=None, allowed_tools=["other.namespace"])
    decision = evaluate_authority(store, actor, task="task", action="x.do", resource="r/1")
    assert decision.reason == AuthorityReason.ACTION_OUTSIDE_CAPABILITY_MANIFEST


# --- delegation attenuation: identity requirements may only tighten --------- #
def test_child_cannot_drop_require_established_identity():
    parent = _lease(require_established_identity=True)
    child = _lease(id="lease-2", require_established_identity=False)
    errors = lease_attenuation_errors(parent, child)
    assert any("require_established_identity" in e for e in errors)


def test_child_cannot_lower_min_assurance_floor():
    parent = _lease(min_assurance=IdentityAssurance.DELEGATED_VERIFIED.value)
    child = _lease(id="lease-2", min_assurance=IdentityAssurance.REPORTED.value)
    errors = lease_attenuation_errors(parent, child)
    assert any("min_assurance" in e for e in errors)


def test_child_may_raise_min_assurance_floor():
    parent = _lease(min_assurance=IdentityAssurance.REPORTED.value)
    child = _lease(id="lease-2", min_assurance=IdentityAssurance.DELEGATED_VERIFIED.value)
    assert lease_attenuation_errors(parent, child) == []


def test_child_cannot_widen_allowed_trust_domains():
    parent = _lease(allowed_trust_domains=["tenant:acme.dev"])
    child = _lease(id="lease-2", allowed_trust_domains=["tenant:acme.dev", "tenant:acme.prod"])
    errors = lease_attenuation_errors(parent, child)
    assert any("allowed_trust_domains" in e for e in errors)


def test_child_cannot_drop_trust_domain_restriction_entirely():
    parent = _lease(allowed_trust_domains=["tenant:acme.dev"])
    child = _lease(id="lease-2", allowed_trust_domains=[])
    errors = lease_attenuation_errors(parent, child)
    assert any("allowed_trust_domains" in e for e in errors)


if __name__ == "__main__":
    test_default_lease_ignores_missing_assurance_entirely()
    test_require_established_identity_denies_unestablished_actor()
    test_require_established_identity_allows_established_actor()
    test_min_assurance_denies_below_floor()
    test_min_assurance_allows_at_or_above_floor()
    test_allowed_trust_domains_denies_outside_domain()
    test_allowed_trust_domains_allows_matching_domain()
    test_capability_gate_still_checked_first_for_backward_compatible_reason_ordering()
    test_child_cannot_drop_require_established_identity()
    test_child_cannot_lower_min_assurance_floor()
    test_child_may_raise_min_assurance_floor()
    test_child_cannot_widen_allowed_trust_domains()
    test_child_cannot_drop_trust_domain_restriction_entirely()
    print("ok")
