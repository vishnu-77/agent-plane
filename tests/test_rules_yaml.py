"""Permissions as YAML: the file round-trips, and a bad file is refused.

A permissions file that is almost right is worse than one that is obviously
wrong, so most of this is about what the loader refuses.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_plane.rules.yaml_io import RulesYamlError, dump_rules, load_rules
from tests.test_accounts import make_project, signup


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("JWT_SECRET", "rules-yaml-secret-0123456789")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


BAD_FILE = """
version: 1
rules:
  - name: oops
    alow: [x]
"""

FILE = """
version: 1
rules:
  - name: Coding agents
    scope:
      agents: ["claude-code"]
    allow: [filesystem.read, tests.execute]
    ask: [filesystem.write, git.push]
    never: [repository.delete]
    resources: ["workspace/*", "github://acme/app"]
    protected_resources: ["workspace/.env*"]
"""


# --------------------------------------------------------------------------- #
# the format
# --------------------------------------------------------------------------- #
def test_a_file_loads_into_rule_payloads():
    [rule] = load_rules(FILE)
    assert rule["name"] == "Coding agents"
    assert rule["scope"] == {"agents": ["claude-code"]}
    assert rule["allow"] == ["filesystem.read", "tests.execute"]
    assert rule["ask"] == ["filesystem.write", "git.push"]
    assert rule["never"] == ["repository.delete"]
    assert rule["protected_resources"] == ["workspace/.env*"]
    assert rule["source"] == "yaml"          # so the console can say where it came from
    assert rule["enabled"] is True


def test_an_empty_document_is_not_an_error():
    assert load_rules("") == []


@pytest.mark.parametrize("text,message", [
    ("rules: [{allow: [x]}]", "needs a name"),
    ("rules: [{name: a}]", "grants and refuses nothing"),
    ("rules: [{name: a, allow: [x], nver: [y]}]", "unknown field"),
    ("rules: [{name: a, allow: [x], scope: {agent: [y]}}]", "unknown field"),
    ("rules: [{name: a, allow: 3}]", "must be a list of strings"),
    ("rules: [{name: a, allow: [x]}, {name: a, never: [y]}]", "duplicate rule name"),
    ("version: 9\nrules: []", "unsupported version"),
    ("- a\n- b", "must be a mapping"),
    ("just a string", "must be a mapping"),
    ("rules: 3", "must be a list"),
    ("{", "not valid YAML"),
])
def test_a_file_that_is_almost_right_is_refused(text, message):
    with pytest.raises(RulesYamlError) as exc:
        load_rules(text)
    assert message in str(exc.value)


def test_a_missing_rules_list_is_refused():
    with pytest.raises(RulesYamlError, match="no 'rules' list"):
        load_rules("version: 1")


# --------------------------------------------------------------------------- #
# the endpoints
# --------------------------------------------------------------------------- #
def test_import_export_round_trips_through_the_api(client):
    signup(client)
    project = make_project(client)
    pid = project["id"]

    imported = client.post("/v1/rules/import", json={"project": pid, "yaml": FILE})
    assert imported.status_code == 200, imported.text
    assert len(imported.json()["created"]) == 1 and imported.json()["updated"] == []

    exported = client.get(f"/v1/rules/export?project={pid}").json()
    assert exported["count"] == 1
    text = exported["yaml"]
    assert "never" in text and "repository.delete" in text
    assert "# agent-plane permissions." in text

    # What comes out goes back in and says the same thing.
    again = load_rules(text)
    assert again[0]["allow"] == ["filesystem.read", "tests.execute"]
    assert again[0]["scope"] == {"agents": ["claude-code"]}


def test_import_merges_by_name_and_leaves_other_rules_alone(client):
    signup(client)
    project = make_project(client)
    pid = project["id"]
    client.post("/v1/rules", json={"project": pid, "name": "Written in the console",
                                   "allow": ["logs.read"]})
    client.post("/v1/rules/import", json={"project": pid, "yaml": FILE})

    changed = FILE.replace("ask: [filesystem.write, git.push]", "ask: [filesystem.write]")
    result = client.post("/v1/rules/import", json={"project": pid, "yaml": changed}).json()
    assert result["created"] == [] and len(result["updated"]) == 1 and result["deleted"] == []

    names = {r["name"] for r in result["rules"]}
    assert names == {"Written in the console", "Coding agents"}
    coding = next(r for r in result["rules"] if r["name"] == "Coding agents")
    assert coding["ask"] == ["filesystem.write"]


def test_replace_makes_the_file_the_whole_truth(client):
    signup(client)
    project = make_project(client)
    pid = project["id"]
    client.post("/v1/rules", json={"project": pid, "name": "Written in the console",
                                   "allow": ["logs.read"]})
    result = client.post("/v1/rules/import",
                         json={"project": pid, "yaml": FILE, "mode": "replace"}).json()
    assert len(result["deleted"]) == 1
    assert [r["name"] for r in result["rules"]] == ["Coding agents"]


def test_a_bad_file_changes_nothing(client):
    signup(client)
    project = make_project(client)
    pid = project["id"]
    client.post("/v1/rules/import", json={"project": pid, "yaml": FILE})
    bad = client.post("/v1/rules/import",
                      json={"project": pid, "yaml": "rules: [{name: b, alow: [x]}]"})
    assert bad.status_code == 400 and "unknown field" in bad.json()["detail"]
    assert len(client.get(f"/v1/rules?project={pid}").json()["rules"]) == 1


def test_imported_rules_decide_actions(client):
    """The file is not decoration: it compiles and the engine uses it."""
    from tests.test_accounts import make_key

    signup(client)
    project = make_project(client, mode="enforce")
    pid = project["id"]
    secret = make_key(client, pid)["secret"]
    auth = {"Authorization": f"Bearer {secret}"}
    client.post("/v1/rules/import", json={"project": pid, "yaml": FILE})

    def report(**event):
        return client.post("/v1/events/action", headers=auth,
                           json={"agent": "claude-code", "task": "t", **event}).json()

    assert report(action="filesystem.read", resource="workspace/src/a.ts")["decision"] == "allow"
    assert report(action="filesystem.write", resource="workspace/src/a.ts")["decision"] == "approval_required"
    assert report(action="repository.delete", resource="github://acme/app")["reason"] == "ACTION_REFUSED_BY_RULE"
    assert report(action="filesystem.read", resource="workspace/.env")["reason"] == "RESOURCE_PROTECTED"


def test_dump_omits_defaults_so_the_file_shows_what_someone_chose(client):
    signup(client)
    project = make_project(client)
    pid = project["id"]
    client.post("/v1/rules", json={"project": pid, "name": "Minimal", "allow": ["logs.read"]})
    text = client.get(f"/v1/rules/export?project={pid}").json()["yaml"]
    assert "allow" in text
    for noise in ("scope:", "resources:", "max_uses", "permitted_consequence", "enabled", "order"):
        assert noise not in text, noise


def test_a_disabled_rule_says_so_in_the_file(client):
    signup(client)
    project = make_project(client)
    pid = project["id"]
    rule = client.post("/v1/rules", json={"project": pid, "name": "Off", "allow": ["logs.read"]}).json()["rule"]
    client.patch(f"/v1/rules/{rule['id']}", json={"enabled": False})
    text = client.get(f"/v1/rules/export?project={pid}").json()["yaml"]
    assert "enabled: false" in text
    assert load_rules(text)[0]["enabled"] is False


def test_a_pipeline_can_apply_the_file_with_a_management_key(client):
    """CI has no cookie. Applying a permissions file is the point of having one."""
    from tests.test_accounts import make_key

    signup(client)
    project = make_project(client)
    pid = project["id"]
    mgmt = make_key(client, pid, name="ci", environment="mgmt")["secret"]
    live = make_key(client, pid, name="laptop")["secret"]
    client.cookies.clear()                     # a pipeline is not signed in

    applied = client.post("/v1/rules/import", headers={"X-Admin-Token": mgmt},
                          json={"project": pid, "yaml": FILE})
    assert applied.status_code == 200, applied.text
    assert len(applied.json()["created"]) == 1

    exported = client.get(f"/v1/rules/export?project={pid}", headers={"X-Admin-Token": mgmt})
    assert exported.status_code == 200 and exported.json()["count"] == 1

    # A project key reports actions; it does not rewrite the project's authority.
    refused = client.post("/v1/rules/import", headers={"X-Admin-Token": live},
                          json={"project": pid, "yaml": FILE})
    assert refused.status_code == 401


def test_the_cli_validates_a_file_without_a_credential(tmp_path):
    from agent_plane.rules.cli import main

    good = tmp_path / "permissions.yaml"
    good.write_text(FILE, encoding="utf-8")
    assert main(["check", str(good)]) == 0

    bad = tmp_path / "bad.yaml"
    bad.write_text(BAD_FILE, encoding="utf-8")
    assert main(["check", str(bad)]) == 1

    assert main(["check", str(tmp_path / "missing.yaml")]) == 1


def test_the_cli_refuses_to_push_without_a_credential(tmp_path, monkeypatch):
    from agent_plane.rules.cli import main

    monkeypatch.delenv("AGENTPLANE_MGMT_KEY", raising=False)
    path = tmp_path / "permissions.yaml"
    path.write_text(FILE, encoding="utf-8")
    assert main(["push", str(path), "--project", "prj_x"]) == 1


def test_dump_is_stable():
    rules = load_rules(FILE)
    from datetime import UTC, datetime

    from agent_plane.rules.store import AuthorityRule

    now = datetime.now(UTC)
    built = [AuthorityRule(id="rule_1", project_id="p", created_at=now, updated_at=now, **r)
             for r in rules]
    assert dump_rules(built) == dump_rules(built)
