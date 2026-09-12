"""Action ingestion, normalization, the three runtime modes, and rules."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_plane.events.normalize import normalize_action
from agent_plane.rules import compile_rules
from agent_plane.rules.store import AuthorityRule, RuleScope, suggest_rule
from tests.test_accounts import make_key, make_project, signup


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("JWT_SECRET", "events-secret-0123456789")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


@pytest.fixture()
def connected(client):
    signup(client)
    project = make_project(client)
    secret = make_key(client, project["id"])["secret"]
    return client, project, {"Authorization": f"Bearer {secret}"}


def report(client, auth, **event):
    r = client.post("/v1/events/action", headers=auth, json=event)
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# normalization
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("event,expected", [
    ({"tool": "Edit", "arguments": {"file_path": "/Users/d/app/src/auth.ts"}},
     ("filesystem.write", "workspace/src/auth.ts")),
    ({"tool": "Read", "arguments": {"file_path": "src/auth.ts"}},
     ("filesystem.read", "workspace/src/auth.ts")),
    ({"tool": "Bash", "arguments": {"command": "npm test"}}, ("tests.execute", "shell/npm")),
    ({"tool": "Bash", "arguments": {"command": "npm install left-pad"}},
     ("package.install", "package/left-pad")),
    ({"tool": "Bash", "arguments": {"command": "ls -la"}}, ("shell.execute", "shell/ls")),
    ({"tool": "WebFetch", "arguments": {"url": "https://api.example.com/v1/x"}},
     ("network.access", "network/api.example.com")),
    ({"tool": "Write", "arguments": {"file_path": ".env"}}, ("filesystem.write", "workspace/.env")),
    ({"tool": "Write", "arguments": {"file_path": "config/.env.local"}},
     ("filesystem.write", "workspace/.env")),
    # An explicit action always wins over anything inferred.
    ({"action": "deployment.restart", "resource": "production/checkout", "tool": "Bash"},
     ("deployment.restart", "production/checkout")),
])
def test_tool_invocations_normalize_to_canonical_actions(event, expected):
    assert normalize_action(**event) == expected


def test_git_commands_resolve_to_repository_actions():
    assert normalize_action(tool="Bash", arguments={"command": "git push origin main"},
                            repository="acme/app", branch="main") == ("git.push", "github://acme/app/branches/main")
    assert normalize_action(tool="Bash", arguments={"command": "git commit -m 'x'"},
                            repository="acme/app")[0] == "git.commit"
    assert normalize_action(tool="Bash", arguments={"command": "gh repo delete acme/app"})[0] == "repository.delete"


def test_paths_never_leak_the_local_filesystem():
    action, resource = normalize_action(tool="Edit", arguments={"file_path": "C:\\Users\\vishnu\\secret\\app\\x.ts"})
    assert action == "filesystem.write"
    # The username and home directory never reach shared evidence.
    assert "Users" not in resource and "vishnu" not in resource
    assert resource == "workspace/secret/app/x.ts"
    assert normalize_action(tool="Edit", arguments={"file_path": "../../etc/passwd"})[1] == "workspace/etc/passwd"
    # A connector that knows its workspace root gets an exactly relative path.
    assert normalize_action(tool="Edit", arguments={"file_path": "/home/dev/proj/lib/x.ts"},
                            workspace_root="/home/dev/proj")[1] == "workspace/lib/x.ts"


# --------------------------------------------------------------------------- #
# ingestion
# --------------------------------------------------------------------------- #
def test_reported_actions_become_decisions_agents_and_integrations(connected):
    client, project, auth = connected
    out = report(client, auth, integration="claude-code", agent="claude-code",
                 task="fix-authentication-tests", tool="Edit",
                 arguments={"file_path": "src/auth.ts"},
                 origin={"kind": "prompt", "ref": "prompt_91B2", "text": "Fix the authentication tests"})
    assert out["action"] == "filesystem.write"
    assert out["decision"] == "simulate" and out["enforced"] is False   # observe
    assert out["mode"] == "observe" and out["binding"] is False
    assert out["consequence"]["environment"] == "workspace"

    task = client.get(f"/v1/tasks/fix-authentication-tests?project={project['id']}").json()
    # Collection policy is off by default: the prompt's id is kept, its text is not.
    assert task["origin"]["ref"] == "prompt_91B2" and task["origin"]["text"] is None

    client.patch(f"/v1/projects/{project['id']}", json={"collection": {"prompt_content": True}})
    report(client, auth, task="second-task", tool="Read", arguments={"file_path": "a.ts"},
           origin={"kind": "prompt", "ref": "p2", "text": "Look at a.ts"})
    assert client.get(f"/v1/tasks/second-task?project={project['id']}").json()["origin"]["text"] == "Look at a.ts"


def test_batch_reporting(connected):
    client, project, auth = connected
    r = client.post("/v1/events/action", headers=auth, json={"events": [
        {"tool": "Read", "arguments": {"file_path": "a.ts"}, "task": "t"},
        {"tool": "Bash", "arguments": {"command": "npm test"}, "task": "t"},
    ]})
    assert [x["action"] for x in r.json()["results"]] == ["filesystem.read", "tests.execute"]
    assert client.get(f"/v1/decisions?project={project['id']}").json()["count"] == 2
    over = client.post("/v1/events/action", headers=auth, json={"events": [{} for _ in range(51)]})
    assert over.status_code == 400


def test_enforcement_capability_is_reported_honestly(connected):
    client, project, auth = connected
    client.patch(f"/v1/projects/{project['id']}", json={"mode": "enforce"})
    sdk = report(client, auth, integration="custom", tool="Bash", arguments={"command": "rm -rf /"}, task="t")
    mcp = report(client, auth, integration="mcp", tool="Bash", arguments={"command": "rm -rf /"}, task="t")
    # Same decision; only the MCP gateway can actually stop it.
    assert sdk["decision"] == mcp["decision"] == "deny"
    assert sdk["enforcement"] == "advisory" and sdk["binding"] is False
    assert mcp["enforcement"] == "full" and mcp["binding"] is True


def test_events_require_a_project_key(client):
    assert client.post("/v1/events/action", json={"tool": "Read"}).status_code == 401
    assert client.post("/v1/events/action", headers={"Authorization": "Bearer ap_live_nope"},
                       json={"tool": "Read"}).status_code == 401


# --------------------------------------------------------------------------- #
# modes
# --------------------------------------------------------------------------- #
def test_observe_govern_enforce(connected):
    client, project, auth = connected
    event = {"integration": "mcp", "agent": "a1", "task": "t1",
             "action": "repository.delete", "resource": "github://acme/app"}

    observed = report(client, auth, **event)
    assert observed["decision"] == "simulate" and observed["would_be"] == "deny"
    assert observed["enforced"] is False and observed["advisory"] is True
    assert "Observe mode" in " ".join(observed["explanation"])

    client.patch(f"/v1/projects/{project['id']}", json={"mode": "govern"})
    governed = report(client, auth, **event)
    assert governed["decision"] == "deny" and governed["enforced"] is False
    assert governed["advisory"] is True and governed["binding"] is False
    assert "Govern mode" in " ".join(governed["explanation"])

    client.patch(f"/v1/projects/{project['id']}", json={"mode": "enforce"})
    enforced = report(client, auth, **event)
    assert enforced["decision"] == "deny" and enforced["enforced"] is True
    assert enforced["binding"] is True and "advisory" not in enforced

    # Every attempt was recorded in all three modes.
    assert client.get(f"/v1/decisions?project={project['id']}").json()["count"] == 3


# --------------------------------------------------------------------------- #
# rules
# --------------------------------------------------------------------------- #
def _rule(**kw):
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    base = {"id": "rule_1", "project_id": "prj_1", "name": "r", "created_at": now, "updated_at": now}
    return AuthorityRule(**{**base, **kw})


def test_rules_compile_allow_ask_never_into_one_lease():
    rules = [_rule(allow=["filesystem.read", "filesystem.write"], ask=["git.push"],
                   never=["repository.delete"], resources=["workspace/*"])]
    lease = compile_rules(rules, project_id="prj_1", agent="claude-code", task="t")
    assert lease is not None
    assert lease.actions == ["filesystem.read", "filesystem.write", "git.push"]
    assert lease.require_approval == ["git.push"]          # ask -> human in the loop
    assert lease.denied_actions == ["repository.delete"]    # never -> absolute
    assert lease.tenant == "prj_1" and lease.subject == "claude-code"
    assert lease.origin["kind"] == "rules"
    # No applicable rule leaves the project default-deny rather than wide open.
    assert compile_rules([], project_id="prj_1", agent="a", task="t") is None
    scoped = [_rule(allow=["a.b"], scope=RuleScope(agents=["codex"]))]
    assert compile_rules(scoped, project_id="prj_1", agent="claude-code", task="t") is None
    assert compile_rules(scoped, project_id="prj_1", agent="codex", task="t") is not None


def test_rules_govern_real_decisions(connected):
    client, project, auth = connected
    client.patch(f"/v1/projects/{project['id']}", json={"mode": "enforce"})
    r = client.post("/v1/rules", json={
        "project": project["id"], "name": "Coding agents",
        "allow": ["filesystem.read", "filesystem.write", "tests.execute"],
        "ask": ["git.push"], "never": ["repository.delete", "credentials.read"],
        "resources": ["workspace/*", "shell/*", "github://*"]})
    assert r.status_code == 200 and r.json()["rule"]["summary"] == "3 allowed · 1 ask first · 2 never"

    allowed = report(client, auth, agent="claude-code", task="t", tool="Edit",
                     arguments={"file_path": "src/auth.ts"})
    assert allowed["decision"] == "allow" and allowed["reason"] == "ACTION_WITHIN_TASK_AUTHORITY"

    asked = report(client, auth, agent="claude-code", task="t", action="git.push",
                   resource="github://acme/app")
    assert asked["decision"] == "approval_required" and asked["approval_id"].startswith("apr_")

    refused = report(client, auth, agent="claude-code", task="t", action="repository.delete",
                     resource="github://acme/app")
    assert refused["decision"] == "deny" and refused["reason"] == "ACTION_REFUSED_BY_RULE"
    assert "NEVER allowed" in " ".join(refused["explanation"])

    outside = report(client, auth, agent="claude-code", task="t", action="deployment.restart",
                     resource="production/checkout")
    # production/checkout is outside every resource the rule scopes, which is a
    # more precise answer than "that action is not granted".
    assert outside["decision"] == "deny" and outside["reason"] == "RESOURCE_OUTSIDE_DELEGATED_SCOPE"


def test_never_cannot_be_granted_back_by_another_rule(connected):
    client, project, auth = connected
    client.patch(f"/v1/projects/{project['id']}", json={"mode": "enforce"})
    client.post("/v1/rules", json={"project": project["id"], "name": "Never",
                                   "never": ["repository.delete"]})
    client.post("/v1/rules", json={"project": project["id"], "name": "Permissive",
                                   "allow": ["repository.delete", "filesystem.read"]})
    refused = report(client, auth, agent="a", task="t", action="repository.delete",
                     resource="github://acme/app")
    assert refused["decision"] == "deny" and refused["reason"] == "ACTION_REFUSED_BY_RULE"
    # The permissive rule still works for everything it is allowed to grant.
    assert report(client, auth, agent="a", task="t", action="filesystem.read",
                  resource="workspace/a.ts")["decision"] == "allow"


def test_editing_a_rule_takes_effect_immediately(connected):
    client, project, auth = connected
    client.patch(f"/v1/projects/{project['id']}", json={"mode": "enforce"})
    rule = client.post("/v1/rules", json={"project": project["id"], "name": "r",
                                          "allow": ["filesystem.read"]}).json()["rule"]
    assert report(client, auth, agent="a", task="t", action="filesystem.write",
                  resource="workspace/a.ts")["decision"] == "deny"
    client.patch(f"/v1/rules/{rule['id']}", json={"allow": ["filesystem.read", "filesystem.write"]})
    assert report(client, auth, agent="a", task="t", action="filesystem.write",
                  resource="workspace/a.ts")["decision"] == "allow"
    client.patch(f"/v1/rules/{rule['id']}", json={"enabled": False})
    assert report(client, auth, agent="a", task="t", action="filesystem.read",
                  resource="workspace/a.ts")["decision"] == "deny"
    assert client.delete(f"/v1/rules/{rule['id']}").status_code == 200


def test_suggested_rules_come_from_observed_behaviour(connected):
    client, project, auth = connected
    for tool, args in [("Read", {"file_path": "a.ts"}), ("Edit", {"file_path": "b.ts"}),
                       ("Bash", {"command": "npm test"})]:
        report(client, auth, agent="claude-code", task="t", tool=tool, arguments=args)
    suggestions = client.get(f"/v1/rules/suggested?project={project['id']}").json()["suggestions"]
    assert len(suggestions) == 1
    draft = suggestions[0]
    assert "filesystem.read" in draft["allow"]           # reads are safe to allow
    assert "filesystem.write" in draft["ask"]             # writes need a human's nod
    assert draft["scope"]["agents"] == ["claude-code"]
    assert draft["source"] == "suggested"


def test_suggestions_drop_what_an_existing_rule_already_decides(connected):
    """Applying a suggestion must remove it: a rule is the human's answer."""
    client, project, auth = connected
    for tool, args in [("Read", {"file_path": "a.ts"}), ("Edit", {"file_path": "b.ts"})]:
        report(client, auth, agent="claude-code", task="t", tool=tool, arguments=args)
    draft = client.get(f"/v1/rules/suggested?project={project['id']}").json()["suggestions"][0]
    client.post("/v1/rules", json={"project": project["id"], "name": "applied",
                                   "scope": {"agents": ["claude-code"]},
                                   "allow": draft["allow"], "ask": draft["ask"], "never": draft["never"]})
    assert client.get(f"/v1/rules/suggested?project={project['id']}").json()["suggestions"] == []

    # New behaviour nobody has ruled on still surfaces.
    report(client, auth, agent="claude-code", task="t", action="git.push",
           resource="github://acme/app/branches/main")
    again = client.get(f"/v1/rules/suggested?project={project['id']}").json()["suggestions"]
    assert len(again) == 1 and "git.push" in again[0]["ask"]
    assert "filesystem.read" not in again[0]["allow"]


def test_out_of_scope_explanation_does_not_invent_authority(connected):
    """An action the rule never granted must not be described as permitted."""
    client, project, auth = connected
    client.post("/v1/rules", json={"project": project["id"], "name": "coding",
                                   "allow": ["filesystem.read"], "resources": ["workspace/src/*"]})
    out = report(client, auth, agent="claude-code", task="t", action="repository.delete",
                 resource="github://acme/app")
    assert out["reason"] == "RESOURCE_OUTSIDE_DELEGATED_SCOPE"
    text = " ".join(out["explanation"])
    assert "permits repository.delete" not in text
    assert "never granted repository.delete" in text


def test_suggest_rule_never_proposes_destructive_actions():
    draft = suggest_rule(project_id="p", observed={"repository.delete": 3, "logs.read": 2},
                         denied={}, resources=["github://a/b"])
    assert draft["never"] == ["repository.delete"] and draft["allow"] == ["logs.read"]
