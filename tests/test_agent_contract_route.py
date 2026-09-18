"""GET /v1/agents/{id}/contract - REST sibling of mcp_control's
current_contract tool, for the console (Phase 15)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

ADMIN = {"X-Admin-Token": "test-admin"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
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


def test_no_contract_found(client):
    r = client.get("/v1/agents/claude-code/contract", headers=ADMIN)
    assert r.status_code == 200
    assert r.json() == {"found": False, "contract": None, "fingerprint": None}


def test_finds_a_software_pack_contract(client):
    body = {"contract_id": "claude-code-software-starter", "agent": "claude-code",
            "allow": ["filesystem.read"], "tenant": "default"}
    issued = client.post("/v1/contracts", headers=ADMIN, json=body)
    assert issued.status_code == 200, issued.text
    r = client.get("/v1/agents/claude-code/contract", headers=ADMIN)
    assert r.status_code == 200
    payload = r.json()
    assert payload["found"] is True
    assert payload["contract"]["contract_id"] == "claude-code-software-starter"
    assert payload["fingerprint"] == issued.json()["fingerprint"]


def test_a_non_owner_project_member_can_read_their_own_contract(client):
    """The actual bug this session's console work surfaced: reading a
    contract for your own agent is a project-scoped read, not a
    deployment-wide operation - require_admin (which only the instance
    owner or ADMIN_TOKEN satisfies) would 403 every normal team member."""
    client.post("/v1/auth/signup", json={"email": "owner@example.com", "password": "correct-horse-battery", "name": "Owner"})
    client.post("/v1/contracts", headers=ADMIN,
               json={"contract_id": "claude-code-software-starter", "agent": "claude-code",
                     "allow": ["filesystem.read"], "tenant": "default"})
    client.post("/v1/auth/logout")
    client.cookies.clear()

    from agent_plane.config import get_settings
    get_settings.cache_clear()
    client.app.state.settings.signup_mode = "open"
    r = client.post("/v1/auth/signup", json={"email": "member@example.com", "password": "correct-horse-battery", "name": "Member"})
    assert r.status_code == 200
    project = client.post("/v1/projects", json={"name": "member-proj", "mode": "observe"}).json()["project"]
    # Seed the contract with no session active - require_admin checks a
    # present session before falling back to ADMIN_TOKEN, so this has to
    # happen logged out, then the member logs back in for the actual read.
    client.cookies.clear()
    seeded = client.post("/v1/contracts", headers=ADMIN,
                         json={"contract_id": "claude-code-software-starter", "agent": "claude-code",
                               "allow": ["filesystem.read"], "tenant": project["id"]})
    assert seeded.status_code == 200, seeded.text
    login = client.post("/v1/auth/login", json={"email": "member@example.com", "password": "correct-horse-battery"})
    assert login.status_code == 200

    # This signed-in user is provably not the instance owner (the first
    # signed-up account is) - no X-Admin-Token, just their session cookie.
    r = client.get(f"/v1/agents/claude-code/contract?tenant={project['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["found"] is True

    # And still can't read a different project's contract.
    r = client.get("/v1/agents/claude-code/contract?tenant=default")
    assert r.status_code == 403
