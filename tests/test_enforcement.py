"""Admission must gate dispatch, without manufacturing authority or execution."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")
pytest.importorskip("jsonschema")
from jsonschema.exceptions import ValidationError
from mcp import types

from agent_plane.authority.evaluator import evaluate_authority
from agent_plane.authority.lease import AuthorityLease
from agent_plane.authority.store import LeaseStore
from agent_plane.enforcement.mapping import GatewayConfig
from agent_plane.enforcement.service import EnforcementService
from agent_plane.schemas.canonical import Actor, Decision, DecisionAction


@pytest.fixture
def setup():
    actor = Actor(user_id="operator", tenant="acme", agent_id="worker", allowed_tools=["branch", "repo.delete"])
    lease = AuthorityLease(id="lease", subject="worker", tenant="acme", task="cleanup", actions=["branch.delete"],
                           resources=["repo/*"], protected_resources=["repo/main"], max_uses={"branch.delete": 1})
    store = LeaseStore([lease])
    events, calls = [], []
    policy = Decision(decision=DecisionAction.ALLOW, decision_id="p", policy_version="test", reason="policy")
    app = SimpleNamespace(state=SimpleNamespace(leases=store, audit=SimpleNamespace(record=events.append),
                          engine=SimpleNamespace(evaluate=lambda request: policy)))
    config = GatewayConfig.model_validate({"upstream_url": "http://127.0.0.1:9999/mcp",
        "bindings": [{"tenant": "acme", "agent": "worker", "task": "cleanup", "lease": "lease"}],
        "tools": [{"name": "repo.delete", "upstream_tool": "delete", "action": "branch.delete", "resource": "repo/{branch}",
                   "input_schema": {"type": "object", "properties": {"branch": {"type": "string"}},
                                    "required": ["branch"], "additionalProperties": False}}]})

    async def upstream(tool, args):
        calls.append(args)
        return types.CallToolResult(content=[types.TextContent(type="text", text="mock done")])

    service = EnforcementService(app, config, upstream)
    return SimpleNamespace(actor=actor, lease=lease, store=store, events=events, calls=calls,
                           policy=policy, service=service, app=app)


def test_protected_override_independent_of_lease_order(setup):
    s = setup
    broad = s.lease.model_copy(update={"id": "broad", "protected_resources": []})
    for leases in ([broad, s.lease], [s.lease, broad]):
        store = LeaseStore(leases)
        result = evaluate_authority(store, s.actor, task="cleanup", action="branch.delete", resource="repo/main")
        assert result.reason.value == "RESOURCE_PROTECTED"
        assert store.use_count("broad", "branch.delete") == 0


def test_preview_and_atomic_one_use(setup):
    s = setup
    for _ in range(3):
        assert evaluate_authority(s.store, s.actor, task="cleanup", action="branch.delete", resource="repo/old", consume=False).allowed
    assert s.store.use_count("lease", "branch.delete") == 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: evaluate_authority(s.store, s.actor, task="cleanup", action="branch.delete", resource="repo/old"), range(20)))
    assert sum(r.allowed for r in results) == 1


@pytest.mark.asyncio
async def test_allow_reserves_once_and_correlates_receipt(setup):
    s = setup
    result = await s.service.invoke(s.actor, "repo.delete", {"branch": "old"}, "request-1")
    assert result["evidence"]["execution_status"] == "completed"
    assert len(s.calls) == 1
    assert s.store.use_count("lease", "branch.delete") == 1
    assert [e["obligations_applied"][0]["execution_status"] for e in s.events] == ["not_dispatched", "dispatch_started", "completed"]
    assert all(e["obligations_applied"][0]["admission_id"] == result["evidence"]["admission_id"] for e in s.events)
    assert await s.service.invoke(s.actor, "repo.delete", {"branch": "old"}, "request-1") == result
    assert len(s.calls) == 1
    with pytest.raises(ValueError, match="conflict"):
        await s.service.invoke(s.actor, "repo.delete", {"branch": "different"}, "request-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("change,branch,reason", [
    ({}, "main", "RESOURCE_PROTECTED"),
    ({"resources": ["elsewhere/*"]}, "old", "RESOURCE_OUTSIDE_DELEGATED_SCOPE"),
    ({"actions": []}, "old", "ACTION_NOT_AUTHORIZED"),
    ({"revoked": True}, "old", "LEASE_REVOKED"),
    ({"expires_at": datetime.now(UTC)-timedelta(seconds=5)}, "old", "LEASE_EXPIRED"),
    ({"require_approval": ["branch.delete"]}, "old", "ACTION_REQUIRES_APPROVAL"),
])
async def test_non_allow_never_dispatches_or_consumes(setup, change, branch, reason):
    s = setup
    s.store.add(s.lease.model_copy(update=change))
    result = await s.service.invoke(s.actor, "repo.delete", {"branch": branch})
    assert result["reason"] == reason
    assert not s.calls
    assert s.store.use_count("lease", "branch.delete") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{}, {"branch": "../main"}, {"branch": "a%2Fmain"}, {"branch": "ok", "task": "admin"}, {"branch": 4}])
async def test_canonical_arguments_cannot_select_task_or_escape_scope(setup, arguments):
    s = setup
    with pytest.raises((ValueError, ValidationError)):
        await s.service.invoke(s.actor, "repo.delete", arguments)
    assert not s.calls


@pytest.mark.asyncio
async def test_binding_capabilities_and_unknown_tools(setup):
    s = setup
    for actor in [s.actor.model_copy(update={"tenant": "other"}), s.actor.model_copy(update={"allowed_tools": []}), s.actor.model_copy(update={"agent_id": "other"})]:
        with pytest.raises(ValueError):
            await s.service.invoke(actor, "repo.delete", {"branch": "old"})
    with pytest.raises(ValueError):
        await s.service.invoke(s.actor, "unmapped", {})
    assert not s.calls


@pytest.mark.asyncio
async def test_policy_denial_and_required_audit_failure_stop_dispatch(setup):
    s = setup
    s.policy.decision = DecisionAction.DENY
    result = await s.service.invoke(s.actor, "repo.delete", {"branch": "old"})
    assert result["decision"] == "deny" and not s.calls
    s.policy.decision = DecisionAction.ALLOW
    def fail(event):
        raise RuntimeError("audit offline")
    s.app.state.audit.record = fail
    with pytest.raises(RuntimeError):
        await s.service.invoke(s.actor, "repo.delete", {"branch": "old"})
    assert not s.calls and s.store.use_count("lease", "branch.delete") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_or_cancelled_dispatch_is_not_reported_as_completed(setup, cancel):
    s = setup
    async def fail(tool, arguments):
        s.calls.append(arguments)
        if cancel:
            raise asyncio.CancelledError()
        raise TimeoutError()
    s.service.upstream = fail
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await s.service.invoke(s.actor, "repo.delete", {"branch": "old"})
    else:
        result = await s.service.invoke(s.actor, "repo.delete", {"branch": "old"})
        assert result["evidence"]["execution_status"] == "outcome_unknown"
    assert s.events[-1]["obligations_applied"][0]["execution_status"] == "outcome_unknown"
    assert len(s.calls) == 1 and s.service.active == 0


@pytest.mark.asyncio
async def test_discovery_is_non_consuming_and_revocation_takes_effect(setup):
    s = setup
    assert s.service.eligible(s.actor, "repo.delete")
    assert s.store.use_count("lease", "branch.delete") == 0
    s.store.revoke("lease")
    assert not s.service.eligible(s.actor, "repo.delete")
    result = await s.service.invoke(s.actor, "repo.delete", {"branch": "old"})
    assert result["decision"] == "deny" and not s.calls


@pytest.mark.asyncio
async def test_policy_denial_hides_tool_and_concurrency_limit_stops_dispatch(setup):
    s = setup
    s.policy.decision = DecisionAction.DENY
    assert not s.service.eligible(s.actor, "repo.delete")
    s.policy.decision = DecisionAction.ALLOW
    s.service.active = s.service.config.max_concurrency
    with pytest.raises(ValueError, match="concurrency limit"):
        await s.service.invoke(s.actor, "repo.delete", {"branch": "old"})
    assert not s.calls and s.store.use_count("lease", "branch.delete") == 0
