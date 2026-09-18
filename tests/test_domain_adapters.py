"""Phase 26/27: DomainAdapter interface + software/cloud packs. Proves the
"core is domain-agnostic" claim end-to-end - a suggested_contract() from
either pack compiles through the same compile_contract() and is enforced
by the same evaluate_authority(), with zero domain-specific code in either."""
from __future__ import annotations

from agent_plane.authority.contract import compile_contract
from agent_plane.authority.evaluator import evaluate_authority
from agent_plane.authority.store import LeaseStore
from agent_plane.domains.base import DomainAdapter
from agent_plane.domains.cloud import CloudOperationsAdapter
from agent_plane.domains.software import SoftwareEngineeringAdapter
from agent_plane.schemas.canonical import Actor


def test_software_adapter_satisfies_the_protocol():
    assert isinstance(SoftwareEngineeringAdapter(), DomainAdapter)


def test_cloud_adapter_satisfies_the_protocol():
    assert isinstance(CloudOperationsAdapter(), DomainAdapter)


def test_software_parse_resource_defaults_into_workspace():
    adapter = SoftwareEngineeringAdapter()
    assert adapter.parse_resource("src/auth.ts") == "src/auth.ts"
    assert adapter.parse_resource("auth.ts") == "workspace/auth.ts"


def test_cloud_parse_resource_defaults_into_staging():
    adapter = CloudOperationsAdapter()
    assert adapter.parse_resource("checkout-svc") == "staging/checkout-svc"
    assert adapter.parse_resource("production/checkout-svc") == "production/checkout-svc"


def test_software_suggested_contract_compiles_and_enforces():
    contract = SoftwareEngineeringAdapter().suggested_contract("claude-code")
    lease = compile_contract(contract, lease_id="lease-sw", task="fix-tests")
    store = LeaseStore()
    store.add(lease)
    actor = Actor(user_id="u1", agent_id="claude-code", allowed_tools=["*"])
    allowed = evaluate_authority(store, actor, task="fix-tests", action="tests.execute", resource="workspace/x")
    assert allowed.allowed
    denied = evaluate_authority(store, actor, task="fix-tests", action="repository.delete", resource="workspace/x")
    assert denied.decision == "deny"


def test_cloud_suggested_contract_compiles_and_enforces():
    contract = CloudOperationsAdapter().suggested_contract("release-bot")
    lease = compile_contract(contract, lease_id="lease-cloud", task="restart-checkout")
    store = LeaseStore()
    store.add(lease)
    actor = Actor(user_id="u1", agent_id="release-bot", allowed_tools=["*"])
    allowed = evaluate_authority(store, actor, task="restart-checkout", action="deployment.restart", resource="staging/checkout")
    assert allowed.allowed
    # production is protected, not just outside scope - explicit denial.
    denied = evaluate_authority(store, actor, task="restart-checkout", action="deployment.restart", resource="production/checkout")
    assert denied.decision == "deny"
    never = evaluate_authority(store, actor, task="restart-checkout", action="infrastructure.destroy", resource="staging/checkout")
    assert never.decision == "deny"


if __name__ == "__main__":
    test_software_adapter_satisfies_the_protocol()
    test_cloud_adapter_satisfies_the_protocol()
    test_software_parse_resource_defaults_into_workspace()
    test_cloud_parse_resource_defaults_into_staging()
    test_software_suggested_contract_compiles_and_enforces()
    test_cloud_suggested_contract_compiles_and_enforces()
    print("ok")
