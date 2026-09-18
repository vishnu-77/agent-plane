"""Phase 19: Agent Plane Control MCP. Exercised through MCPServer's real
call_tool()/list_tools() dispatch (not by calling the Python functions
directly), so the SDK wiring itself is actually verified against this
project's real, pinned SDK (pyproject.toml: mcp==2.2.0)."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from agent_plane.audit.store import SqlAuditStore
from agent_plane.authority.lease import AuthorityLease
from agent_plane.authority.contract import ContractRegistry
from agent_plane.authority.service import TRACE_SCHEMA
from agent_plane.authority.store import SqlLeaseStore
from agent_plane.mcp_control.server import build_control_server
from agent_plane.registry.store import MemoryRegistry


def _state(tmp_path):
    return SimpleNamespace(
        agent_registry=MemoryRegistry(),
        leases=SqlLeaseStore(f"sqlite:///{tmp_path / 'leases.db'}"),
        audit=SqlAuditStore(f"sqlite:///{tmp_path / 'audit.db'}", "test-signing-key"),
        contracts=ContractRegistry(),
    )


def _run(coro):
    return asyncio.run(coro)


def test_control_server_lists_all_four_tools(tmp_path):
    mcp = build_control_server(_state(tmp_path))
    tools = _run(mcp.list_tools())
    assert {t.name for t in tools} == {"whoami", "authority", "explain_decision", "current_contract"}


def test_whoami_not_found_via_real_dispatch(tmp_path):
    mcp = build_control_server(_state(tmp_path))
    result = _run(mcp.call_tool("whoami", {"agent_id": "nobody"}))
    assert result.structured_content == {"found": False}


def test_whoami_finds_an_observed_agent(tmp_path):
    state = _state(tmp_path)
    state.agent_registry.observe(tenant="default", agent="claude-code", application="app",
                                 declared=["repo.read"], task="t1", action="repo.read",
                                 resource="repo/x", outcome="allow", decision_id="dec_1",
                                 context={"framework": "claude-code"},
                                 assurance="connector_authenticated", trust_domain="tenant:default")
    mcp = build_control_server(state)
    result = _run(mcp.call_tool("whoami", {"agent_id": "claude-code"}))
    payload = result.structured_content
    assert payload["found"] is True
    assert payload["assurance"] == "connector_authenticated"
    assert payload["lifecycle_state"] in ("identified", "owned", "authorised", "active")


def test_authority_reflects_granted_leases(tmp_path):
    state = _state(tmp_path)
    state.leases.add(AuthorityLease(id="l1", task="t1", subject="agt", tenant="default",
                                    resources=["r/*"], actions=["x.read"]))
    mcp = build_control_server(state)
    result = _run(mcp.call_tool("authority", {"agent_id": "agt"}))
    assert result.structured_content["granted"] == ["x.read"]


def test_explain_decision_not_found(tmp_path):
    mcp = build_control_server(_state(tmp_path))
    result = _run(mcp.call_tool("explain_decision", {"decision_id": "nope"}))
    assert result.structured_content == {"found": False}


def test_explain_decision_returns_the_recorded_trace(tmp_path):
    state = _state(tmp_path)
    state.audit.record({
        "decision_id": "az_test1", "user_id": "u1", "tenant": "default", "department": None,
        "app_id": None, "agent_id": "agt", "model_requested": "authorize:x.read", "model_used": "x.read",
        "data_classification": "", "decision": "deny", "reason": "ACTION_NOT_AUTHORIZED", "rules_matched": [],
        "obligations_applied": [{"schema": TRACE_SCHEMA, "decision": {"outcome": "deny", "reason": "ACTION_NOT_AUTHORIZED"}}],
    })
    mcp = build_control_server(state)
    result = _run(mcp.call_tool("explain_decision", {"decision_id": "az_test1"}))
    payload = result.structured_content
    assert payload["found"] is True
    assert payload["reason"] == "ACTION_NOT_AUTHORIZED"
    assert payload["trace"]["decision"]["outcome"] == "deny"


def test_current_contract_found_by_domain_pack_naming_convention(tmp_path):
    from agent_plane.domains.software import SoftwareEngineeringAdapter
    state = _state(tmp_path)
    contract = SoftwareEngineeringAdapter().suggested_contract("claude-code")
    state.contracts.upsert(contract)
    mcp = build_control_server(state)
    result = _run(mcp.call_tool("current_contract", {"agent_id": "claude-code"}))
    payload = result.structured_content
    assert payload["found"] is True
    assert payload["contract"]["contract_id"] == "claude-code-software-starter"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
