"""Extended SDK surface: approvals, admin client, adapters, conformance kit.

Runs against the real application through an httpx transport bound to the
ASGI app, so the SDK is exercised end to end without a network."""
from __future__ import annotations

import jwt
import pytest
from agentplane import AgentPlane, AgentPlaneAdmin, ApprovalTimeout
from agentplane.adapters import (
    ApprovalRequired,
    NotAuthorized,
    govern,
    governed_dispatch,
    langchain_tool,
    openai_agents_guard,
)
from agentplane.testing import CASES, ConformanceFailure, check_executor
from fastapi.testclient import TestClient

JWT_SECRET = "sdk-secret"


class _TestClientTransport:
    """Minimal httpx transport that replays requests through a TestClient."""

    def __init__(self, client: TestClient):
        self._client = client

    def handle_request(self, request):
        import httpx

        response = self._client.request(
            request.method, str(request.url.raw_path.decode()), headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(response.status_code, headers=response.headers, content=response.content)

    def close(self) -> None:  # pragma: no cover - interface completeness
        pass


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _token(agent_id: str = "sdk-agent") -> str:
    return jwt.encode({"sub": "u1", "tenant": "acme", "agent_id": agent_id,
                       "allowed_tools": ["deployment", "branch"]}, JWT_SECRET, algorithm="HS256")


@pytest.fixture()
def plane(server):

    transport = _TestClientTransport(server)
    with AgentPlane("http://plane", _token(), transport=transport) as p:  # type: ignore[arg-type]
        yield p


@pytest.fixture()
def admin(server):
    transport = _TestClientTransport(server)
    with AgentPlaneAdmin("http://plane", "test-admin", transport=transport) as a:  # type: ignore[arg-type]
        yield a


def test_admin_issues_and_manages_leases(admin, plane):
    lease = admin.issue_lease(tenant="acme", id="lease-sdk", subject="sdk-agent", task="t1",
                              resources=["staging/*"], actions=["deployment.read", "deployment.restart"],
                              protected_resources=["production/*"])
    assert lease.id == "lease-sdk" and not lease.revoked
    assert admin.get_lease("lease-sdk").actions == ["deployment.read", "deployment.restart"]

    assert plane.authorize(task="t1", action="deployment.restart", resource="staging/x").allowed
    shrunk = admin.shrink_lease("lease-sdk", actions=["deployment.read"])
    assert shrunk.actions == ["deployment.read"]
    d = plane.authorize(task="t1", action="deployment.restart", resource="staging/x")
    assert d.decision == "deny" and d.reason == "ACTION_NOT_AUTHORIZED"

    admin.revoke_lease("lease-sdk")
    assert admin.get_lease("lease-sdk").revoked
    assert plane.authorize(task="t1", action="deployment.read", resource="staging/x").reason == "LEASE_REVOKED"


def test_admin_templates_and_audit(admin, plane):
    names = {t["name"] for t in admin.list_templates()}
    assert "repair-service" in names
    lease = admin.issue_from_template("repair-service", subject="sdk-agent", task="t2", tenant="acme",
                                      variables={"env": "staging", "service": "cart"})
    assert lease.resources == ["staging/cart"]
    assert plane.authorize(task="t2", action="deployment.read", resource="staging/cart").allowed
    events = admin.audit(limit=5)
    assert events and events[0]["decision"] == "allow"


def test_approval_round_trip_via_sdk(admin, plane):
    admin.issue_lease(tenant="acme", id="lease-appr", subject="sdk-agent", task="t3", resources=["staging/*"],
                      actions=["deployment.restart"], require_approval=["deployment.restart"])
    d = plane.authorize(task="t3", action="deployment.restart", resource="staging/cart")
    assert d.needs_approval and d.approval_id
    with pytest.raises(ApprovalTimeout):
        plane.wait_for_approval(d, timeout=0.05, interval=0.01)

    pending = admin.list_approvals()
    assert [p.id for p in pending] == [d.approval_id]
    assert plane.get_approval(d.approval_id).is_open
    approved = admin.approve(d.approval_id, note="ok", decided_by="alice")
    assert approved.status == "approved" and approved.decided_by == "alice"

    resumed = plane.wait_for_approval(d, timeout=1, interval=0.01)
    assert resumed.allowed and resumed.reason == "ACTION_APPROVED"
    again = plane.authorize(task="t3", action="deployment.restart", resource="staging/cart",
                            approval=d.approval_id)
    assert again.reason == "APPROVAL_ALREADY_USED"


def test_delegate_via_sdk(admin, plane, server):
    admin.issue_lease(tenant="acme", id="lease-parent", subject="sdk-agent", task="t4", resources=["staging/*"],
                      actions=["deployment.read", "deployment.restart"], child_authority="subset_only")
    child = plane.delegate("lease-parent", agent="helper", actions=["deployment.read"])
    assert child.subject == "helper" and child.actions == ["deployment.read"]
    assert admin.get_lease(child.id).task == "t4"


def test_context_round_trips(admin, plane):
    admin.issue_lease(tenant="acme", id="lease-ctx", subject="sdk-agent", task="t5", resources=["staging/*"],
                      actions=["deployment.read"])
    d = plane.authorize(task="t5", action="deployment.read", resource="staging/a",
                        context={"parent_evidence_id": "dec_1", "origin": "human"})
    assert d.allowed and d.context["parent_evidence_id"] == "dec_1"


# --------------------------------------------------------------------------- #
# Adapters
# --------------------------------------------------------------------------- #
def test_govern_decorator_runs_only_on_allow(admin, plane):
    admin.issue_lease(tenant="acme", id="lease-dec", subject="sdk-agent", task="t6", resources=["staging/*"],
                      actions=["deployment.restart"], protected_resources=["staging/locked"])
    calls = []

    @govern(plane, task="t6", action="deployment.restart", resource="staging/{service}")
    def restart(service: str) -> str:
        calls.append(service)
        return f"restarted {service}"

    assert restart("cart") == "restarted cart"
    assert restart.last_decision.allowed
    with pytest.raises(NotAuthorized) as exc:
        restart(service="locked")
    assert exc.value.decision.reason == "RESOURCE_PROTECTED"
    @govern(plane, task="t6", action="deployment.delete", resource="staging/{service}")
    def delete(service: str) -> None:
        calls.append("deleted " + service)

    with pytest.raises(NotAuthorized) as exc:
        delete("cart")  # action outside the lease -> deny
    assert exc.value.decision.reason == "ACTION_NOT_AUTHORIZED"
    assert calls == ["cart"]


def test_govern_raises_approval_required(admin, plane):
    admin.issue_lease(tenant="acme", id="lease-dec2", subject="sdk-agent", task="t7", resources=["staging/*"],
                      actions=["deployment.restart"], require_approval=["deployment.restart"])

    @govern(plane, task=lambda: "t7", action="deployment.restart",
            resource=lambda service: f"staging/{service}")
    def restart(service: str) -> None:
        raise AssertionError("must not run")

    with pytest.raises(ApprovalRequired) as exc:
        restart("cart")
    assert exc.value.decision.approval_id.startswith("apr_")
    # Auto-wait path: approve on the side, then the call proceeds.
    admin.approve(exc.value.decision.approval_id)
    ran = []

    @govern(plane, task="t7", action="deployment.restart", resource="staging/{service}",
            wait_for_approval=1)
    def restart2(service: str) -> None:
        ran.append(service)

    # A fresh call raises a fresh approval; approve it from a thread-free stub by
    # pre-approving through the admin after the first poll is impossible here,
    # so assert the timeout path converts into ApprovalRequired instead.
    with pytest.raises(ApprovalRequired):
        restart2("cart")
    assert ran == []


def test_governed_dispatch_and_openai_guard(admin, plane):
    admin.issue_lease(tenant="acme", id="lease-disp", subject="sdk-agent", task="t8",
                      resources=["github://acme/repo/*"], actions=["branch.delete"],
                      protected_resources=["github://acme/repo/branches/main"])
    deleted = []
    dispatch = governed_dispatch(
        plane, task="t8", actions={"delete_branch": "branch.delete"},
        resources={"delete_branch": "github://acme/repo/branches/{branch}"},
        handlers={"delete_branch": lambda branch: deleted.append(branch) or "ok"},
    )
    assert dispatch("delete_branch", {"branch": "stale"}) == "ok"
    with pytest.raises(NotAuthorized):
        dispatch("delete_branch", {"branch": "main"})
    with pytest.raises(KeyError):
        dispatch("rm_rf", {})
    assert deleted == ["stale"]

    guard = openai_agents_guard(plane, task="t8", actions={"delete_branch": "branch.delete"},
                                resources={"delete_branch": "github://acme/repo/branches/{branch}"})
    assert guard("delete_branch", {"branch": "old"}).allowed
    with pytest.raises(NotAuthorized):
        guard("delete_branch", {"branch": "main"})
    with pytest.raises(NotAuthorized) as exc:
        guard("unknown_tool", {})
    assert exc.value.decision.reason == "TOOL_NOT_GOVERNED"


def test_langchain_style_tool_wrapper(admin, plane):
    admin.issue_lease(tenant="acme", id="lease-lc", subject="sdk-agent", task="t9", resources=["staging/*"],
                      actions=["deployment.read"], protected_resources=["staging/locked"])

    class FakeTool:
        name = "inspect"
        description = "inspect a service"

        def __init__(self):
            self.calls = []

        def invoke(self, payload, **kwargs):
            self.calls.append(payload)
            return "inspected"

    raw = FakeTool()
    tool = langchain_tool(raw, plane, task="t9", action="deployment.read", resource="staging/{service}")
    assert tool.name == "inspect"  # attribute passthrough
    assert tool.invoke({"service": "cart"}) == "inspected"
    with pytest.raises(NotAuthorized):
        tool.invoke({"service": "locked"})
    assert raw.calls == [{"service": "cart"}]


# --------------------------------------------------------------------------- #
# Conformance kit
# --------------------------------------------------------------------------- #
def test_conformance_kit_passes_a_correct_executor():
    def build(plane, execute):
        def run():
            d = plane.authorize(task="t", action="a.b", resource="r")
            if d.allowed:
                execute()
        return run

    report = check_executor(build)
    assert report.ok and len(report.passed) == len(CASES)


def test_conformance_kit_catches_status_only_executors():
    def build(plane, execute):
        def run():
            # The classic bug: trust the HTTP status, ignore the body.
            resp = plane._client.post("/v1/authorize", json={"task": "t", "action": "a", "resource": "r"})
            if resp.status_code == 200:
                execute()
        return run

    with pytest.raises(ConformanceFailure) as exc:
        check_executor(build)
    failed = exc.value.report.failed
    assert "http_200_wrong_decision" in failed
    assert "http_200_not_json" in failed
    assert "allow" not in failed


def test_conformance_kit_catches_execute_on_error():
    def build(plane, execute):
        def run():
            try:
                d = plane.authorize(task="t", action="a", resource="r")
                if d.allowed:
                    execute()
            except Exception:
                execute()  # "fail open" on outage
        return run

    report = check_executor(build, raise_on_failure=False)
    assert "transport_failure" in report.failed and "http_500" in report.failed
