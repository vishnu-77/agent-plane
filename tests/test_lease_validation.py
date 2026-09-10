"""Regression tests for two crash paths in lease handling.

Both were reachable from a normal admin request and both left the affected
subject+task permanently broken (HTTP 500 on every subsequent /v1/authorize)
rather than failing the bad request.
"""
from __future__ import annotations

from datetime import UTC, datetime

import jwt
import pytest
from fastapi.testclient import TestClient

from agent_plane.authority.lease import AuthorityLease, parse_lease

JWT_SECRET = "lease-validation-secret"
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


def _auth(**claims) -> dict[str, str]:
    token = jwt.encode(
        {"sub": "u1", "tenant": "default", "agent_id": "shrink-agent", **claims},
        JWT_SECRET, algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _issue(client, **overrides):
    doc = {
        "id": "lease-shrink-target", "task": "t-shrink", "subject": "shrink-agent",
        "resources": ["staging/*"], "actions": ["deployment.restart"],
        **overrides,
    }
    r = client.post("/v1/leases", json=doc, headers=ADMIN)
    assert r.status_code == 200, r.text
    return doc


# --- PATCH /v1/leases/{id} must validate the merged document -----------------

def test_patch_rejects_a_malformed_expires_at(client):
    """`model_copy(update=...)` does not validate in pydantic v2. A string here
    used to be stored straight into a datetime field."""
    _issue(client)
    r = client.patch(
        "/v1/leases/lease-shrink-target",
        json={"expires_at": "not-a-timestamp"}, headers=ADMIN,
    )
    assert r.status_code == 400, r.text


def test_patch_rejects_a_malformed_max_uses(client):
    _issue(client)
    r = client.patch(
        "/v1/leases/lease-shrink-target",
        json={"max_uses": {"deployment.restart": "lots"}}, headers=ADMIN,
    )
    assert r.status_code == 400, r.text


def test_authorize_still_works_after_a_rejected_patch(client):
    """The real damage: a poisoned lease used to 500 every later authorize
    call for that subject+task, for the life of the process."""
    _issue(client)
    client.patch(
        "/v1/leases/lease-shrink-target",
        json={"expires_at": "not-a-timestamp"}, headers=ADMIN,
    )
    r = client.post(
        "/v1/authorize",
        json={"task": "t-shrink", "action": "deployment.restart",
              "resource": "staging/checkout"},
        headers=_auth(),
    )
    assert r.status_code != 500, r.text
    assert r.json()["decision"] == "allow"


# --- naive datetimes must never reach the evaluator -------------------------

def test_naive_expiry_is_normalized_to_utc():
    lease = AuthorityLease(
        id="l1", task="t", subject="a",
        expires_at=datetime(2027, 1, 1, 0, 0, 0),  # naive
    )
    assert lease.expires_at.tzinfo is not None


def test_naive_expiry_from_a_yaml_manifest_is_normalized():
    lease = parse_lease({
        "id": "l2", "task": "t", "subject": "a",
        "constraints": {"expires_at": "2027-01-01T00:00:00"},  # no Z, no offset
    })
    assert lease.expires_at.tzinfo is not None
    assert lease.expires_at > datetime.now(UTC)


def test_authorize_with_a_naive_expiry_lease_does_not_500(client):
    _issue(client, expires_at="2027-01-01T00:00:00")
    r = client.post(
        "/v1/authorize",
        json={"task": "t-shrink", "action": "deployment.restart",
              "resource": "staging/checkout"},
        headers=_auth(),
    )
    assert r.status_code != 500, r.text
