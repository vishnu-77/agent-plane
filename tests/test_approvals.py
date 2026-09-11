"""The approval loop: 202 -> queue -> approve/reject -> one-shot resume."""
from __future__ import annotations

import json

import jwt
import pytest
from fastapi.testclient import TestClient

from agent_plane.approvals.notify import ApprovalNotifier, verify_signature

JWT_SECRET = "approval-secret"
ADMIN = {"X-Admin-Token": "test-admin"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
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


def _token(agent_id: str = "ops-agent") -> str:
    return jwt.encode({"sub": "u1", "tenant": "acme", "agent_id": agent_id,
                       "allowed_tools": ["deployment"]}, JWT_SECRET, algorithm="HS256")


def _auth(agent_id: str = "ops-agent") -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(agent_id)}"}


def _issue(client, **overrides):
    lease = {
        "id": "lease-approval", "subject": "ops-agent", "task": "restart-checkout",
        "resources": ["staging/*"], "actions": ["deployment.restart", "deployment.read"],
        "require_approval": ["deployment.restart"],
    }
    lease.update(overrides)
    r = client.post("/v1/leases", headers=ADMIN, json=lease)
    assert r.status_code == 200, r.text
    return lease


def _authorize(client, action="deployment.restart", resource="staging/checkout", **extra):
    return client.post("/v1/authorize", headers=_auth(),
                       json={"task": "restart-checkout", "action": action, "resource": resource, **extra})


def test_approval_required_creates_pending_request(client):
    _issue(client)
    r = _authorize(client)
    assert r.status_code == 202
    detail = r.json()["detail"]
    assert detail["decision"] == "approval_required"
    assert detail["approval_id"].startswith("apr_")

    queue = client.get("/v1/approvals", headers=ADMIN).json()
    assert queue["count"] == 1
    item = queue["approvals"][0]
    assert item["id"] == detail["approval_id"]
    assert item["status"] == "pending"
    assert item["evidence_id"] == detail["evidence_id"]
    assert (item["task"], item["action"], item["resource"]) == (
        "restart-checkout", "deployment.restart", "staging/checkout")


def test_approve_then_resume_allows_exactly_once(client):
    _issue(client)
    approval_id = _authorize(client).json()["detail"]["approval_id"]

    # Resuming while still pending is still a 202.
    r = _authorize(client, approval=approval_id)
    assert r.status_code == 202
    assert r.json()["detail"]["reason"] == "APPROVAL_PENDING"

    r = client.post(f"/v1/approvals/{approval_id}/approve", headers=ADMIN,
                    json={"note": "verified with on-call", "decided_by": "alice"})
    assert r.status_code == 200
    assert r.json()["approval"]["status"] == "approved"
    assert r.json()["approval"]["decided_by"] == "alice"

    r = _authorize(client, approval=approval_id)
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "allow"
    assert body["reason"] == "ACTION_APPROVED"
    assert body["approval_id"] == approval_id
    assert body["lease"] == "lease-approval"

    # A second resume must not authorise a second execution.
    r = _authorize(client, approval=approval_id)
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "APPROVAL_ALREADY_USED"

    assert client.get(f"/v1/approvals/{approval_id}", headers=ADMIN).json()["status"] == "consumed"


def test_reject_denies_resume(client):
    _issue(client)
    approval_id = _authorize(client).json()["detail"]["approval_id"]
    r = client.post(f"/v1/approvals/{approval_id}/reject", headers=ADMIN, json={"note": "no"})
    assert r.status_code == 200
    r = _authorize(client, approval=approval_id)
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "APPROVAL_REJECTED"


def test_decision_is_final(client):
    _issue(client)
    approval_id = _authorize(client).json()["detail"]["approval_id"]
    assert client.post(f"/v1/approvals/{approval_id}/approve", headers=ADMIN).status_code == 200
    r = client.post(f"/v1/approvals/{approval_id}/reject", headers=ADMIN)
    assert r.status_code == 409
    assert r.json()["detail"]["status"] == "approved"


def test_approval_cannot_be_reused_for_a_different_action(client):
    _issue(client)
    approval_id = _authorize(client).json()["detail"]["approval_id"]
    client.post(f"/v1/approvals/{approval_id}/approve", headers=ADMIN)
    r = _authorize(client, resource="staging/payments", approval=approval_id)
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "APPROVAL_MISMATCH"
    # The mismatch did not spend the approval.
    assert client.get(f"/v1/approvals/{approval_id}", headers=ADMIN).json()["status"] == "approved"


def test_revoked_lease_beats_granted_approval(client):
    _issue(client)
    approval_id = _authorize(client).json()["detail"]["approval_id"]
    client.post(f"/v1/approvals/{approval_id}/approve", headers=ADMIN)
    assert client.delete("/v1/leases/lease-approval", headers=ADMIN).status_code == 200
    r = _authorize(client, approval=approval_id)
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "LEASE_REVOKED"
    assert client.get(f"/v1/approvals/{approval_id}", headers=ADMIN).json()["status"] == "approved"


def test_another_agent_cannot_use_or_see_the_approval(client):
    _issue(client)
    approval_id = _authorize(client).json()["detail"]["approval_id"]
    client.post(f"/v1/approvals/{approval_id}/approve", headers=ADMIN)
    assert client.get(f"/v1/approvals/{approval_id}", headers=_auth("other-agent")).status_code == 404
    assert client.get(f"/v1/approvals/{approval_id}", headers=_auth()).status_code == 200
    r = client.post("/v1/authorize", headers=_auth("other-agent"), json={
        "task": "restart-checkout", "action": "deployment.restart",
        "resource": "staging/checkout", "approval": approval_id})
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "APPROVAL_NOT_FOUND"


def test_unknown_approval_and_admin_gating(client):
    _issue(client)
    r = _authorize(client, approval="apr_nope")
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "APPROVAL_NOT_FOUND"
    assert client.get("/v1/approvals").status_code == 401
    assert client.post("/v1/approvals/apr_nope/approve", headers=ADMIN).status_code == 404


def test_approval_decisions_are_audited(client):
    _issue(client)
    approval_id = _authorize(client).json()["detail"]["approval_id"]
    client.post(f"/v1/approvals/{approval_id}/approve", headers=ADMIN, json={"decided_by": "bob"})
    events = client.get("/v1/audit?limit=10", headers=ADMIN).json()
    reasons = [e["reason"] for e in (events["events"] if isinstance(events, dict) else events)]
    assert "APPROVAL_GRANTED" in reasons


def test_webhook_events_are_signed(client, monkeypatch):
    sent: list[tuple[str, bytes, dict]] = []
    notifier = ApprovalNotifier("https://hooks.example/approvals", "dev-audit-key-change-me",
                                sender=lambda url, body, headers: sent.append((url, body, headers)),
                                synchronous=True)
    client.app.state.approval_notifier = notifier
    _issue(client)
    approval_id = _authorize(client).json()["detail"]["approval_id"]
    client.post(f"/v1/approvals/{approval_id}/approve", headers=ADMIN)

    assert [json.loads(b)["event"] for _, b, _ in sent] == ["approval.requested", "approval.approved"]
    url, body, headers = sent[1]
    assert url == "https://hooks.example/approvals"
    assert verify_signature("dev-audit-key-change-me", body, headers["X-AgentPlane-Signature"])
    assert not verify_signature("wrong-key", body, headers["X-AgentPlane-Signature"])
    assert json.loads(body)["approval"]["id"] == approval_id


def test_failed_webhook_never_blocks_the_decision(client):
    def boom(url, body, headers):
        raise RuntimeError("receiver down")

    client.app.state.approval_notifier = ApprovalNotifier(
        "https://hooks.example/x", "k", sender=boom, synchronous=True)
    _issue(client)
    assert _authorize(client).status_code == 202
