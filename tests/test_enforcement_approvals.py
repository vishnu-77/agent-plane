"""MCP gateway approval loop: an APPROVAL_REQUIRED admission raises a request,
and a granted approval lets the identical call dispatch exactly once."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")
pytest.importorskip("jsonschema")
from mcp import types

from agent_plane.approvals.notify import ApprovalNotifier
from agent_plane.approvals.store import MemoryApprovalStore
from agent_plane.authority.lease import AuthorityLease
from agent_plane.authority.store import LeaseStore
from agent_plane.enforcement.mapping import GatewayConfig
from agent_plane.enforcement.service import EnforcementService
from agent_plane.schemas.canonical import Actor, Decision, DecisionAction


@pytest.fixture
def setup():
    actor = Actor(user_id="operator", tenant="acme", agent_id="worker", allowed_tools=["branch"])
    lease = AuthorityLease(id="lease", subject="worker", task="cleanup", actions=["branch.delete"],
                           resources=["repo/*"], require_approval=["branch.delete"],
                           max_uses={"branch.delete": 2})
    store = LeaseStore([lease])
    events, calls, hooks = [], [], []
    policy = Decision(decision=DecisionAction.ALLOW, decision_id="p", policy_version="test", reason="policy")
    notifier = ApprovalNotifier("https://hooks.test/x", "k", sender=lambda u, b, h: hooks.append(b),
                                synchronous=True)
    app = SimpleNamespace(state=SimpleNamespace(
        leases=store, audit=SimpleNamespace(record=events.append),
        engine=SimpleNamespace(evaluate=lambda request: policy),
        approvals=MemoryApprovalStore(), approval_notifier=notifier,
        settings=SimpleNamespace(approval_ttl_seconds=600)))
    config = GatewayConfig.model_validate({"upstream_url": "http://127.0.0.1:9999/mcp",
        "bindings": [{"tenant": "acme", "agent": "worker", "task": "cleanup", "lease": "lease"}],
        "tools": [{"name": "repo.delete", "upstream_tool": "delete", "action": "branch.delete",
                   "resource": "repo/{branch}",
                   "input_schema": {"type": "object", "properties": {"branch": {"type": "string"}},
                                    "required": ["branch"], "additionalProperties": False}}]})

    async def upstream(tool, args):
        calls.append(args)
        return types.CallToolResult(content=[types.TextContent(type="text", text="done")])

    service = EnforcementService(app, config, upstream)
    return SimpleNamespace(actor=actor, store=store, events=events, calls=calls, hooks=hooks,
                           service=service, app=app)


def test_gateway_raises_and_resumes_approval(setup):
    s = setup
    first = asyncio.run(s.service.invoke(s.actor, "repo.delete", {"branch": "stale"}))
    assert first["decision"] == "approval_required"
    approval_id = first["evidence"]["approval_id"]
    assert approval_id.startswith("apr_")
    assert s.calls == []
    pending = s.app.state.approvals.list(status="pending")
    assert [p.id for p in pending] == [approval_id]
    assert pending[0].context["tool"] == "repo.delete"
    assert len(s.hooks) == 1

    # Resume before a decision: still pending, still not dispatched.
    again = asyncio.run(s.service.invoke(s.actor, "repo.delete", {"branch": "stale"}, None, approval_id))
    assert again["decision"] == "approval_required" and again["reason"] == "APPROVAL_PENDING"
    assert s.calls == []

    s.app.state.approvals.decide(approval_id, status="approved", decided_by="alice", note=None)
    resumed = asyncio.run(s.service.invoke(s.actor, "repo.delete", {"branch": "stale"}, None, approval_id))
    assert resumed["decision"] == "allow" and resumed["reason"] == "ACTION_APPROVED"
    assert resumed["result"] is not None
    assert s.calls == [{"branch": "stale"}]
    assert s.store.use_count("lease", "branch.delete") == 1

    # The approval is spent; a second resume cannot dispatch again.
    third = asyncio.run(s.service.invoke(s.actor, "repo.delete", {"branch": "stale"}, None, approval_id))
    assert third["decision"] == "deny" and third["reason"] == "APPROVAL_ALREADY_USED"
    assert s.calls == [{"branch": "stale"}]


def test_approval_is_bound_to_exact_arguments(setup):
    s = setup
    first = asyncio.run(s.service.invoke(s.actor, "repo.delete", {"branch": "stale"}))
    approval_id = first["evidence"]["approval_id"]
    s.app.state.approvals.decide(approval_id, status="approved", decided_by="alice", note=None)
    other = asyncio.run(s.service.invoke(s.actor, "repo.delete", {"branch": "other"}, None, approval_id))
    assert other["decision"] == "deny" and other["reason"] == "APPROVAL_MISMATCH"
    assert s.calls == []
    assert s.app.state.approvals.get(approval_id).status == "approved"  # not spent


def test_revoked_lease_beats_approval_on_gateway(setup):
    s = setup
    first = asyncio.run(s.service.invoke(s.actor, "repo.delete", {"branch": "stale"}))
    approval_id = first["evidence"]["approval_id"]
    s.app.state.approvals.decide(approval_id, status="approved", decided_by="alice", note=None)
    s.store.revoke("lease")
    resumed = asyncio.run(s.service.invoke(s.actor, "repo.delete", {"branch": "stale"}, None, approval_id))
    assert resumed["decision"] == "deny" and resumed["reason"] == "LEASE_REVOKED"
    assert s.calls == []


def test_rejected_approval_denies(setup):
    s = setup
    first = asyncio.run(s.service.invoke(s.actor, "repo.delete", {"branch": "stale"}))
    approval_id = first["evidence"]["approval_id"]
    s.app.state.approvals.decide(approval_id, status="rejected", decided_by="alice", note="no")
    resumed = asyncio.run(s.service.invoke(s.actor, "repo.delete", {"branch": "stale"}, None, approval_id))
    assert resumed["decision"] == "deny" and resumed["reason"] == "APPROVAL_REJECTED"
    assert s.calls == []


def test_invalid_approval_id_is_refused(setup):
    s = setup
    with pytest.raises(ValueError):
        asyncio.run(s.service.invoke(s.actor, "repo.delete", {"branch": "stale"}, None, ""))
    with pytest.raises(ValueError):
        asyncio.run(s.service.invoke(s.actor, "repo.delete", {"branch": "stale"}, None, "x" * 65))
