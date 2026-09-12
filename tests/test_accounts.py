"""Sign-up, projects, and API keys: the path a developer actually walks.

The acceptance test at the bottom is the product promise - sign up, create a
project, generate a key, connect an agent, see the first action - with no JWT,
no lease YAML, no admin token anywhere in it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_plane.accounts.security import (
    generate_api_key,
    hash_api_key,
    hash_password,
    read_session,
    sign_session,
    verify_password,
)


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


def signup(client, email="dev@example.com", password="correct-horse-battery"):
    r = client.post("/v1/auth/signup", json={"email": email, "password": password, "name": "Dev"})
    assert r.status_code == 200, r.text
    return r.json()


def make_project(client, name="personal-coding", mode="observe"):
    r = client.post("/v1/projects", json={"name": name, "mode": mode})
    assert r.status_code == 200, r.text
    return r.json()["project"]


def make_key(client, project_id, name="personal-laptop", environment="live"):
    r = client.post("/v1/api-keys", json={"project": project_id, "name": name, "environment": environment})
    assert r.status_code == 200, r.text
    return r.json()


def test_only_the_instance_owner_gets_deployment_wide_operations(client):
    """A second account on a shared instance is not an administrator."""
    signup(client)                                     # the first account owns the instance
    make_project(client)
    assert client.get("/admin/policies").status_code == 200

    client.post("/v1/auth/logout")
    client.cookies.clear()
    # A second account exists only if sign-up is open; create it through the store.
    accounts = client.app.state.accounts
    second = accounts.create_user(email="second@example.com", password="correct-horse-battery")
    workspace = accounts.create_workspace(name="second", owner=second.id)
    accounts.create_project(workspace_id=workspace.id, name="theirs", created_by=second.id)
    assert accounts.is_instance_owner(second.id) is False

    r = client.post("/v1/auth/login", json={"email": "second@example.com",
                                            "password": "correct-horse-battery"})
    assert r.status_code == 200, r.text
    refused = client.get("/admin/policies")
    assert refused.status_code == 403
    assert "deployment-wide" in refused.json()["detail"]


def test_demo_stays_readable_while_signed_in(client):
    """The console keeps a LIVE/DEMO toggle after sign-in.

    A demo read therefore arrives with a session cookie attached. Resolving the
    session first would scope the caller to their own project and refuse the
    demo tenant they asked for, so the toggle must keep working.
    """
    signup(client)
    make_project(client)
    headers = {"X-Demo-Token": "demo"}
    for path in ("/v1/decisions?tenant=prj_demo&limit=5", "/v1/agents?tenant=prj_demo",
                 "/v1/approvals?tenant=prj_demo&status=pending"):
        assert client.get(path, headers=headers).status_code == 200, path
    # A wrong token is still refused, and demo remains read-only.
    assert client.get("/v1/decisions?tenant=prj_demo", headers={"X-Demo-Token": "nope"}).status_code == 403


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
def test_passwords_are_salted_and_verifiable():
    stored = hash_password("correct-horse-battery")
    assert stored.startswith("scrypt$") and "correct-horse" not in stored
    assert verify_password("correct-horse-battery", stored)
    assert not verify_password("wrong", stored)
    # A second hash of the same password differs: the salt is per-password.
    assert stored != hash_password("correct-horse-battery")


def test_api_keys_are_prefixed_random_and_stored_only_as_a_hash():
    plaintext, prefix, last4 = generate_api_key("live")
    assert plaintext.startswith("ap_live_") and prefix == "ap_live_"
    assert plaintext.endswith(last4) and len(plaintext) > 30
    digest = hash_api_key(plaintext, "server-secret")
    assert plaintext not in digest and len(digest) == 64
    assert digest == hash_api_key(plaintext, "server-secret")           # deterministic
    assert digest != hash_api_key(plaintext, "another-secret")          # keyed
    assert generate_api_key("live")[0] != plaintext                      # random


def test_session_cookies_are_signed_and_expire():
    cookie = sign_session({"sub": "usr_1"}, "secret", 60)
    assert read_session(cookie, "secret")["sub"] == "usr_1"
    assert read_session(cookie, "other-secret") is None
    body, mac = cookie.rsplit(".", 1)
    assert read_session(f"{body}x.{mac}", "secret") is None
    assert read_session(sign_session({"sub": "usr_1"}, "secret", -1), "secret") is None
    assert read_session(None, "secret") is None


# --------------------------------------------------------------------------- #
# sign-up and session
# --------------------------------------------------------------------------- #
def test_first_run_signup_then_closed(client):
    state = client.get("/v1/auth/state").json()
    assert state["first_run"] is True and state["signup_open"] is True

    body = signup(client)
    assert body["user"]["email"] == "dev@example.com"
    assert body["workspace"]["id"].startswith("wsp_")

    # first_user mode: the door closes behind the first account.
    assert client.get("/v1/auth/state").json()["signup_open"] is False
    second = client.post("/v1/auth/signup", json={"email": "other@example.com", "password": "another-long-one"})
    assert second.status_code == 403

    me = client.get("/v1/auth/me").json()
    assert me["user"]["email"] == "dev@example.com" and me["onboarded"] is False
    assert client.post("/v1/auth/logout").status_code == 200
    client.cookies.clear()
    assert client.get("/v1/auth/me").status_code == 401
    assert client.post("/v1/auth/login", json={"email": "dev@example.com", "password": "nope"}).status_code == 401
    assert client.post("/v1/auth/login", json={"email": "dev@example.com",
                                               "password": "correct-horse-battery"}).status_code == 200
    assert client.get("/v1/auth/me").status_code == 200


def test_signup_validates_email_and_password(client):
    assert client.post("/v1/auth/signup", json={"email": "nope", "password": "long-enough-here"}).status_code == 400
    r = client.post("/v1/auth/signup", json={"email": "a@b.com", "password": "short"})
    assert r.status_code == 400 and "at least 10" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# projects and keys
# --------------------------------------------------------------------------- #
def test_projects_default_to_observe_and_can_change_mode(client):
    signup(client)
    project = make_project(client)
    assert project["mode"] == "observe"
    assert project["id"].startswith("prj_")

    r = client.patch(f"/v1/projects/{project['id']}", json={"mode": "enforce"})
    assert r.json()["project"]["mode"] == "enforce"
    assert client.patch(f"/v1/projects/{project['id']}", json={"mode": "nonsense"}).status_code == 400
    # Collection policy: content stays off unless a human turns it on.
    detail = client.get(f"/v1/projects/{project['id']}").json()
    assert detail["project"]["collection"]["prompt_content"] is False
    assert detail["project"]["collection"]["action_name"] is True
    r = client.patch(f"/v1/projects/{project['id']}", json={"collection": {"prompt_content": True}})
    assert r.json()["project"]["collection"]["prompt_content"] is True


def test_api_key_lifecycle(client):
    signup(client)
    project = make_project(client)
    created = make_key(client, project["id"])
    secret = created["secret"]
    assert secret.startswith("ap_live_")
    assert created["key"]["masked"].startswith("ap_live_") and "•" in created["key"]["masked"]
    assert created["key"]["status"] == "active" and created["key"]["last_used_at"] is None

    # The secret is returned exactly once and never appears in any listing.
    listing = client.get(f"/v1/api-keys?project={project['id']}").json()["keys"]
    assert len(listing) == 1 and "secret" not in listing[0]
    assert secret not in str(listing)

    # Using it authenticates and stamps last_used_at.
    ping = client.post("/v1/events/action", headers={"Authorization": f"Bearer {secret}"},
                       json={"tool": "Read", "arguments": {"file_path": "src/auth.ts"}})
    assert ping.status_code == 200
    assert client.get(f"/v1/api-keys?project={project['id']}").json()["keys"][0]["last_used_at"]

    rotated = client.post(f"/v1/api-keys/{created['key']['id']}/rotate").json()
    assert rotated["secret"] != secret and rotated["key"]["id"] != created["key"]["id"]
    # The old secret stops working the moment it is rotated.
    assert client.post("/v1/events/action", headers={"Authorization": f"Bearer {secret}"},
                       json={"tool": "Read"}).status_code == 401
    assert client.post("/v1/events/action", headers={"Authorization": f"Bearer {rotated['secret']}"},
                       json={"tool": "Read"}).status_code == 200

    client.delete(f"/v1/api-keys/{rotated['key']['id']}")
    assert client.post("/v1/events/action", headers={"Authorization": f"Bearer {rotated['secret']}"},
                       json={"tool": "Read"}).status_code == 401


def test_keys_and_projects_are_isolated_between_users(client):
    signup(client)
    mine = make_project(client)
    secret = make_key(client, mine["id"])["secret"]
    client.post("/v1/auth/logout")
    client.cookies.clear()

    # A second account (open signup for the sake of the test) sees nothing of the first.
    from agent_plane.config import get_settings
    get_settings.cache_clear()
    client.app.state.settings.signup_mode = "open"
    signup(client, email="other@example.com")
    assert client.get("/v1/auth/me").json()["projects"] == []
    assert client.get(f"/v1/api-keys?project={mine['id']}").status_code == 403
    assert client.patch(f"/v1/projects/{mine['id']}", json={"mode": "enforce"}).status_code == 403
    # ... and its own key cannot report into someone else's project.
    theirs = make_project(client, name="other-project")
    other_secret = make_key(client, theirs["id"])["secret"]
    r = client.post("/v1/events/action", headers={"Authorization": f"Bearer {other_secret}"},
                    json={"tool": "Read", "arguments": {"file_path": "a.ts"}})
    assert r.json()["evidence_id"]
    assert client.get(f"/v1/agents?project={theirs['id']}").json()["count"] == 1
    assert secret != other_secret


# --------------------------------------------------------------------------- #
# the acceptance test
# --------------------------------------------------------------------------- #
def test_five_steps_to_first_activity(client):
    """Sign up, create a project, generate a key, connect, see the action.

    No JWT, no AuthorityLease, no policy YAML, no admin token.
    """
    signup(client)                                             # 1
    project = make_project(client)                              # 2
    secret = make_key(client)["secret"] if False else make_key(client, project["id"])["secret"]  # 3

    handshake = client.post("/v1/auth/exchange", headers={"Authorization": f"Bearer {secret}"},
                            json={"integration": "claude-code", "host": "macbook-pro"})
    assert handshake.status_code == 200                          # 4
    session = handshake.json()
    assert session["project"]["mode"] == "observe"
    assert session["integration"]["observation"] == "full"
    assert session["collect"]["prompt_content"] is False

    reported = client.post("/v1/events/action", headers={"Authorization": f"Bearer {secret}"},
                           json={"integration": "claude-code", "agent": "claude-code",
                                 "task": "fix-authentication-tests", "tool": "Edit",
                                 "arguments": {"file_path": "/Users/dev/app/src/auth.ts"}})
    assert reported.status_code == 200                            # 5
    event = reported.json()
    assert event["action"] == "filesystem.write" and event["resource"] == "workspace/src/auth.ts"

    activity = client.get(f"/v1/decisions?project={project['id']}").json()["decisions"]
    assert len(activity) == 1 and activity[0]["agent"] == "claude-code"
    agents = client.get(f"/v1/agents?project={project['id']}").json()["agents"]
    assert agents[0]["id"] == "claude-code" and agents[0]["current_task"] == "fix-authentication-tests"
    integrations = client.get(f"/v1/integrations?project={project['id']}").json()["integrations"]
    assert integrations[0]["status"] == "connected" and integrations[0]["host"] == "macbook-pro"
    assert client.get("/v1/auth/me").json()["onboarded"] is True
