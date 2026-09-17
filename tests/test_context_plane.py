"""Context-plane provenance, drift, lineage, and cold-start-friendly console APIs."""
from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from agent_plane.context.store import MemoryContextStore


def test_context_store_versions_only_real_drift():
    store = MemoryContextStore()
    first = store.register_many("prj_1", [{
        "kind": "instruction",
        "name": "AGENTS.md",
        "source": "repo://AGENTS.md",
        "digest": "sha256:first",
        "trust": "repository",
        "influence": "high",
    }], agent="coder", task="fix-tests")[0]
    assert first.version == 1 and first.change_count == 0
    assert len(store.snapshots("prj_1", first.id)) == 1

    same = store.register_many("prj_1", [{
        "kind": "instruction", "name": "AGENTS.md", "source": "repo://AGENTS.md",
        "digest": "sha256:first", "trust": "repository", "influence": "high",
    }], agent="reviewer", task="review")[0]
    assert same.version == 1 and same.change_count == 0
    assert set(same.agents) == {"coder", "reviewer"}
    assert len(store.snapshots("prj_1", first.id)) == 1

    changed = store.register_many("prj_1", [{
        "kind": "instruction", "name": "AGENTS.md", "source": "repo://AGENTS.md",
        "digest": "sha256:second", "trust": "repository", "influence": "high",
    }])[0]
    assert changed.version == 2 and changed.change_count == 1
    assert changed.previous_digest == "sha256:first"
    assert len(store.snapshots("prj_1", first.id)) == 2


def test_context_lineage_is_tenant_scoped():
    store = MemoryContextStore()
    asset = store.register_many("prj_a", [{
        "kind": "skill", "name": "deploy", "source": "repo://.claude/skills/deploy/SKILL.md",
        "digest": "sha256:deploy", "trust": "repository", "influence": "high",
    }])[0]
    linked = store.link_decision("prj_a", "dec_1", [asset.id], task="deploy", agent="release")
    assert linked.asset_ids == [asset.id]
    assert store.lineage("prj_a", "dec_1") is not None
    assert store.lineage("prj_b", "dec_1") is None


def test_context_exposure_is_attention_not_boolean_policy():
    store = MemoryContextStore()
    external = store.register_many("p", [{
        "kind": "mcp", "name": "external-admin", "source": "mcp://external-admin",
        "trust": "external", "influence": "high",
    }])[0]
    internal = store.register_many("p", [{
        "kind": "instruction", "name": "AGENTS.md", "source": "repo://AGENTS.md",
        "trust": "verified", "influence": "medium",
    }])[0]
    assert 0 <= external.exposure_score <= 100
    assert 0 <= internal.exposure_score <= 100
    assert external.exposure_score > internal.exposure_score
    assert not hasattr(external, "decision")  # authority remains a separate subsystem


def test_coding_context_discovery_hashes_but_never_sends_contents(tmp_path, monkeypatch):
    from agent_plane.connect import hook

    (tmp_path / "AGENTS.md").write_text("NEVER SEND THIS INSTRUCTION BODY")
    skill_dir = tmp_path / ".claude" / "skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("SECRET SKILL INSTRUCTIONS")
    home = tmp_path / ".agentplane-test"
    monkeypatch.setenv("AGENTPLANE_HOME", str(home))
    monkeypatch.setattr(hook, "_repo_root", lambda cwd: tmp_path)

    assets = hook._discover_context(str(tmp_path), "claude-code")
    by_source = {a["source"]: a for a in assets}
    assert "repo://AGENTS.md" in by_source
    assert "repo://.claude/skills/deploy/SKILL.md" in by_source
    assert by_source["repo://AGENTS.md"]["digest"] == (
        "sha256:" + hashlib.sha256(b"NEVER SEND THIS INSTRUCTION BODY").hexdigest()
    )
    serialized = repr(assets)
    assert "NEVER SEND THIS INSTRUCTION BODY" not in serialized
    assert "SECRET SKILL INSTRUCTIONS" not in serialized


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("JWT_SECRET", "context-plane-test-secret-0123456789")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _signup_project_key(client: TestClient):
    signup = client.post("/v1/auth/signup", json={
        "email": "context@example.com", "password": "correct-horse-battery", "name": "Context Dev",
    })
    assert signup.status_code == 200, signup.text
    project_resp = client.post("/v1/projects", json={"name": "context-app", "mode": "observe"})
    assert project_resp.status_code == 200, project_resp.text
    project = project_resp.json()["project"]
    key_resp = client.post("/v1/api-keys", json={
        "project": project["id"], "name": "context-test", "environment": "live",
    })
    assert key_resp.status_code == 200, key_resp.text
    return project, key_resp.json()["secret"]


def test_auth_and_console_bootstrap_collapse_cold_start_requests(client):
    signed_out = client.get("/v1/auth/bootstrap")
    assert signed_out.status_code == 200
    assert signed_out.json()["me"] is None
    project, _ = _signup_project_key(client)

    auth = client.get("/v1/auth/bootstrap").json()
    assert auth["me"]["user"]["email"] == "context@example.com"
    boot = client.get(f"/v1/console/bootstrap?project={project['id']}")
    assert boot.status_code == 200, boot.text
    assert set(boot.json()) == {"system", "decisions", "agents", "approvals"}


def test_action_event_registers_context_and_exposes_decision_lineage(client):
    project, secret = _signup_project_key(client)
    headers = {"Authorization": f"Bearer {secret}"}
    event = client.post("/v1/events/action", headers=headers, json={
        "integration": "claude-code",
        "agent": "claude-code",
        "task": "fix-auth-tests",
        "tool": "Edit",
        "arguments": {"file_path": "/workspace/src/auth.ts"},
        "context_assets": [{
            "kind": "instruction",
            "name": "AGENTS.md",
            "source": "repo://AGENTS.md",
            "digest": "sha256:abc",
            "trust": "repository",
            "influence": "high",
        }],
    })
    assert event.status_code == 200, event.text
    body = event.json()
    assert body["context_assets"]

    inventory = client.get(f"/v1/context/assets?project={project['id']}")
    assert inventory.status_code == 200, inventory.text
    sources = {a["source"] for a in inventory.json()["assets"]}
    assert "repo://AGENTS.md" in sources
    assert any(s.startswith("claude-code://") for s in sources)

    detail = client.get(f"/v1/decisions/{body['evidence_id']}")
    assert detail.status_code == 200, detail.text
    lineage = detail.json()["context_lineage"]
    assert lineage["lineage"]["decision_id"] == body["evidence_id"]
    assert {a["id"] for a in lineage["assets"]} == set(lineage["lineage"]["asset_ids"])


def test_memory_write_uses_authority_service_and_records_lineage(client):
    project, secret = _signup_project_key(client)
    response = client.post("/v1/context/memory/authorize", headers={"Authorization": f"Bearer {secret}"}, json={
        "operation": "write",
        "agent": "support-agent",
        "task": "support-case-19",
        "provider": "openterra",
        "scope": "support-agent",
        "record_ref": "case-19-summary",
        "classification": "internal",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action"] == "memory.write"
    assert body["resource"].startswith("memory://openterra/support-agent")
    lineage = client.get(f"/v1/context/lineage/{body['evidence_id']}")
    assert lineage.status_code == 200, lineage.text
    assert lineage.json()["assets"][0]["kind"] == "memory"
    assert lineage.json()["lineage"]["tenant"] == project["id"]
