"""Phase 17: device/browser authentication. CLI never sees a password;
approval happens in an already-authenticated browser session, reusing the
existing session-cookie auth and API-key minting - not a new credential
type."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("JWT_SECRET", "accounts-secret-0123456789")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    from agent_plane.config import get_settings
    get_settings.cache_clear()
    from agent_plane.main import create_app
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _signup_and_project(client, email="dev@example.com"):
    r = client.post("/v1/auth/signup", json={"email": email, "password": "correct-horse-battery", "name": "Dev"})
    assert r.status_code == 200, r.text
    project = client.post("/v1/projects", json={"name": "proj", "mode": "observe"}).json()["project"]
    return project


def test_exchange_before_approval_is_pending(client):
    start = client.post("/v1/device/start").json()
    r = client.post("/v1/device/exchange", json={"device_code": start["device_code"]})
    assert r.status_code == 428
    assert r.json()["detail"]["error"] == "authorization_pending"


def test_full_flow_start_approve_exchange(client):
    project = _signup_and_project(client)
    start = client.post("/v1/device/start").json()
    assert "-" in start["user_code"]

    approve = client.post("/v1/device/approve", json={"user_code": start["user_code"], "project": project["id"]})
    assert approve.status_code == 200, approve.text

    exchanged = client.post("/v1/device/exchange", json={"device_code": start["device_code"]})
    assert exchanged.status_code == 200
    secret = exchanged.json()["secret"]
    assert secret.startswith("ap_live_")
    assert exchanged.json()["project"] == project["id"]

    # The minted key actually works.
    handshake = client.post("/v1/auth/exchange", headers={"Authorization": f"Bearer {secret}"},
                            json={"integration": "claude-code"})
    assert handshake.status_code == 200


def test_exchange_is_single_use(client):
    project = _signup_and_project(client)
    start = client.post("/v1/device/start").json()
    client.post("/v1/device/approve", json={"user_code": start["user_code"], "project": project["id"]})
    first = client.post("/v1/device/exchange", json={"device_code": start["device_code"]})
    assert first.status_code == 200
    second = client.post("/v1/device/exchange", json={"device_code": start["device_code"]})
    assert second.status_code == 410


def test_denied_code_is_reported_to_the_cli(client):
    _signup_and_project(client)
    start = client.post("/v1/device/start").json()
    deny = client.post("/v1/device/deny", json={"user_code": start["user_code"]})
    assert deny.status_code == 200
    exchanged = client.post("/v1/device/exchange", json={"device_code": start["device_code"]})
    assert exchanged.status_code == 403
    assert exchanged.json()["detail"]["error"] == "access_denied"


def test_cannot_approve_a_project_you_do_not_belong_to(client):
    _signup_and_project(client)
    start = client.post("/v1/device/start").json()
    r = client.post("/v1/device/approve", json={"user_code": start["user_code"], "project": "some-other-project"})
    assert r.status_code in (403, 404)


def test_double_approval_is_rejected(client):
    project = _signup_and_project(client)
    start = client.post("/v1/device/start").json()
    first = client.post("/v1/device/approve", json={"user_code": start["user_code"], "project": project["id"]})
    assert first.status_code == 200
    second = client.post("/v1/device/approve", json={"user_code": start["user_code"], "project": project["id"]})
    assert second.status_code == 409


def test_unknown_user_code_is_404(client):
    _signup_and_project(client)
    r = client.post("/v1/device/approve", json={"user_code": "ZZZZ-ZZZZ", "project": "whatever"})
    assert r.status_code in (403, 404)  # project check or code lookup, either way not approved


def test_approval_requires_a_session(client):
    project_id = "irrelevant"
    start = client.post("/v1/device/start").json()
    r = client.post("/v1/device/approve", json={"user_code": start["user_code"], "project": project_id})
    assert r.status_code == 401


def test_approval_page_without_session_does_not_leak_the_code_status(client):
    r = client.get("/device", params={"code": "AAAA-BBBB"})
    assert r.status_code == 401


def test_approval_page_renders_the_form_when_signed_in(client):
    project = _signup_and_project(client)
    start = client.post("/v1/device/start").json()
    r = client.get("/device", params={"code": start["user_code"]})
    assert r.status_code == 200
    assert start["user_code"] in r.text
    assert project["id"] in r.text
