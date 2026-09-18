"""Phase 13/14: Authority Contract model, compiler (-> AuthorityLease, no
second engine), and versioning (contract_id/version/fingerprint/supersedes,
so "which policy allowed this on <date>" has an exact answer)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_plane.authority.contract import (
    AuthorityContract,
    ContractRegistry,
    compile_contract,
    parse_contract,
)
from agent_plane.authority.evaluator import evaluate_authority
from agent_plane.authority.lease import lease_attenuation_errors
from agent_plane.authority.store import LeaseStore
from agent_plane.schemas.canonical import Actor

YAML_EXAMPLE = {
    "contract_id": "claude-code-coding", "agent": "claude-code",
    "allow": ["filesystem.read", "filesystem.write", "tests.execute"],
    "ask_first": ["git.push"],
    "never": ["repository.delete", "secrets.read"],
    "resources": {"allow": ["workspace/**"], "protected": ["workspace/.env*", "production/**"]},
    "consequence": {"environments": ["development"], "max_reversibility": ["reversible"]},
    "delegation": {"children": "subset_only"},
}


def test_parse_contract_from_the_spec_example_shape():
    contract = parse_contract(YAML_EXAMPLE)
    assert contract.agent == "claude-code"
    assert set(contract.allow) == {"filesystem.read", "filesystem.write", "tests.execute"}
    assert contract.ask_first == ["git.push"]
    assert contract.never == ["repository.delete", "secrets.read"]
    assert contract.resources_allow == ["workspace/**"]
    assert contract.resources_protected == ["workspace/.env*", "production/**"]
    assert contract.consequence_environments == ["development"]
    assert contract.max_reversibility == "reversible"  # unwrapped from the example's one-item list


def test_parse_contract_requires_contract_id_and_agent():
    with pytest.raises(ValueError):
        parse_contract({"allow": ["x.read"]})


def test_compile_produces_an_equivalent_lease_the_evaluator_actually_enforces():
    contract = parse_contract(YAML_EXAMPLE)
    lease = compile_contract(contract, lease_id="lease-1", task="fix-tests")
    assert lease.actions == contract.allow
    assert lease.denied_actions == contract.never
    assert lease.require_approval == contract.ask_first
    assert lease.resources == contract.resources_allow
    assert lease.protected_resources == contract.resources_protected
    assert lease.permitted_consequence == {"environments": ["development"], "max_reversibility": "reversible"}

    store = LeaseStore()
    store.add(lease)
    actor = Actor(user_id="u1", agent_id="claude-code", allowed_tools=["*"])
    allowed = evaluate_authority(store, actor, task="fix-tests", action="filesystem.write", resource="workspace/x")
    assert allowed.allowed
    denied = evaluate_authority(store, actor, task="fix-tests", action="repository.delete", resource="workspace/x")
    assert denied.decision == "deny"
    protected = evaluate_authority(store, actor, task="fix-tests", action="filesystem.write", resource="production/x")
    assert protected.decision == "deny"


def test_fingerprint_is_deterministic_and_content_based():
    a = parse_contract(YAML_EXAMPLE)
    b = parse_contract({**YAML_EXAMPLE, "created_by": "someone-else"})  # bookkeeping differs
    assert a.fingerprint == b.fingerprint  # same governance content
    c = parse_contract({**YAML_EXAMPLE, "never": [*YAML_EXAMPLE["never"], "repository.create"]})
    assert a.fingerprint != c.fingerprint  # different governance content


def test_registry_auto_increments_version_and_tracks_history():
    registry = ContractRegistry()
    v1 = parse_contract(YAML_EXAMPLE)
    registry.upsert(v1)
    v2 = v1.model_copy(update={"version": 2, "never": [*v1.never, "repository.create"]})
    registry.upsert(v2)
    assert registry.latest("default", "claude-code-coding").version == 2
    assert [c.version for c in registry.history("default", "claude-code-coding")] == [1, 2]


def test_at_answers_which_version_was_effective_on_a_date():
    registry = ContractRegistry()
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    t1 = datetime(2026, 9, 18, tzinfo=UTC)
    v1 = parse_contract(YAML_EXAMPLE).model_copy(update={"created_at": t0, "effective_at": t0})
    v2 = v1.model_copy(update={"version": 2, "created_at": t1, "effective_at": t1,
                               "never": [*v1.never, "repository.create"]})
    registry.upsert(v1)
    registry.upsert(v2)
    assert registry.at("default", "claude-code-coding", as_of=t0 + timedelta(days=1)).version == 1
    assert registry.at("default", "claude-code-coding", as_of=t1 + timedelta(days=1)).version == 2
    assert registry.at("default", "claude-code-coding", as_of=t0 - timedelta(days=1)) is None


def test_contract_identity_prerequisite_passthrough_compiles_and_enforces():
    contract = AuthorityContract(contract_id="strict", agent="release-bot",
                                 allow=["deployment.restart"], resources_allow=["prod/*"],
                                 min_assurance="delegated_verified")
    lease = compile_contract(contract, lease_id="lease-1", task="deploy")
    store = LeaseStore()
    store.add(lease)
    from agent_plane.identity.assurance import IdentityAssurance
    weak_actor = Actor(user_id="u1", agent_id="release-bot", assurance=IdentityAssurance.REPORTED)
    decision = evaluate_authority(store, weak_actor, task="deploy", action="deployment.restart", resource="prod/x")
    assert decision.decision == "deny"
    strong_actor = Actor(user_id="u1", agent_id="release-bot", assurance=IdentityAssurance.DELEGATED_VERIFIED)
    decision = evaluate_authority(store, strong_actor, task="deploy", action="deployment.restart", resource="prod/x")
    assert decision.allowed


def test_contract_compiled_leases_still_obey_delegation_attenuation():
    """A contract is just a friendlier way to author a lease - the lease it
    produces is a normal lease, still subject to the same attenuation rule
    as a hand-written one."""
    contract = parse_contract(YAML_EXAMPLE)
    parent = compile_contract(contract, lease_id="lease-1", task="fix-tests")
    child = parent.model_copy(update={"id": "lease-2", "actions": [*parent.actions, "secrets.read"]})
    errors = lease_attenuation_errors(parent, child)
    assert any("secrets.read" in e for e in errors)


if __name__ == "__main__":
    test_parse_contract_from_the_spec_example_shape()
    test_parse_contract_requires_contract_id_and_agent()
    test_compile_produces_an_equivalent_lease_the_evaluator_actually_enforces()
    test_fingerprint_is_deterministic_and_content_based()
    test_registry_auto_increments_version_and_tracks_history()
    test_at_answers_which_version_was_effective_on_a_date()
    test_contract_identity_prerequisite_passthrough_compiles_and_enforces()
    test_contract_compiled_leases_still_obey_delegation_attenuation()
    print("ok")
