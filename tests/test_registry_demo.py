"""Agent registry, lineage, observe/enforce, quarantine, and the demo harness."""
from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient

JWT_SECRET = "registry-secret"
ADMIN = {"X-Admin-Token": "test-admin"}
DEMO = {"X-Demo-Token": "demo"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")
    monkeypatch.setenv("DEMO_ENABLED", "true")
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _auth(agent="inc-agent", tenant="acme", tools=("logs", "metrics", "deployment")):
    tok = jwt.encode({"sub": "u1", "tenant": tenant, "agent_id": agent, "app_id": "ops-app",
                      "allowed_tools": list(tools)}, JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


def _lease(client, **overrides):
    doc = {"id": "lease-inc", "subject": "inc-agent", "tenant": "acme", "task": "incident-1",
           "resources": ["staging/*"], "actions": ["logs.read", "deployment.restart"],
           "origin": {"kind": "human", "created_by": "oncall", "text": "fix staging, not production"},
           "child_authority": "subset_only"}
    doc.update(overrides)
    assert client.post("/v1/leases", headers=ADMIN, json=doc).status_code == 200
    return doc


# --------------------------------------------------------------------------- #
# Registry discovery
# --------------------------------------------------------------------------- #
def test_agents_are_discovered_from_traffic(client):
    _lease(client)
    client.post("/v1/tasks", headers=_auth(), json={"task": "incident-1", "origin": {
        "kind": "prompt", "ref": "prompt_1", "text": "Investigate checkout in staging", "created_by": "human:oncall"}})
    for action, resource in [("logs.read", "staging/checkout"), ("deployment.restart", "staging/checkout"),
                             ("deployment.restart", "production/checkout"), ("metrics.read", "staging/checkout")]:
        client.post("/v1/authorize", headers=_auth(), json={"task": "incident-1", "action": action,
                                                            "resource": resource, "context": {"framework": "langgraph"}})
    agents = client.get("/v1/agents?tenant=acme", headers=ADMIN).json()["agents"]
    assert [a["id"] for a in agents] == ["inc-agent"]
    agent = agents[0]
    assert agent["application"] == "ops-app" and agent["framework"] == "langgraph"
    assert agent["current_task"] == "incident-1"
    assert agent["declared_capabilities"] == ["deployment", "logs", "metrics"]
    assert agent["granted_authority"] == ["deployment.restart", "logs.read"]
    assert agent["requested_authority"] == {"logs.read": 1, "deployment.restart": 2, "metrics.read": 1}
    assert agent["exercised_authority"] == {"logs.read": 1, "deployment.restart": 1}
    assert agent["denied_authority"] == {"deployment.restart": 1, "metrics.read": 1}
    assert agent["decisions"] == {"allow": 2, "deny": 2}
    assert agent["active_lease"] == "lease-inc"

    detail = client.get("/v1/agents/inc-agent?tenant=acme", headers=ADMIN).json()
    assert detail["drift"]["ungranted"] == ["metrics.read"]
    assert detail["drift"]["undeclared"] == []
    assert detail["lineage"]["lease-inc"][0]["origin"]["created_by"] == "oncall"
    assert len(detail["recent_decisions"]) == 4

    task = client.get("/v1/tasks/incident-1?tenant=acme", headers=ADMIN).json()
    assert task["origin"]["text"].startswith("Investigate")
    assert task["leases"] == ["lease-inc"] and task["agents"] == ["inc-agent"]
    assert task["observed_actions"]["deployment.restart"] == 2

    resources = client.get("/v1/resources?tenant=acme", headers=ADMIN).json()["resources"]
    names = {r["resource"] for r in resources}
    assert {"staging/checkout", "production/checkout"} <= names
    system = client.get("/v1/system?tenant=acme", headers=ADMIN).json()
    assert system["agents"] == 1 and system["decisions"] == {"allow": 2, "deny": 2} and system["mode"] == "enforce"


def test_suggested_lease_from_observation(client):
    _lease(client)
    for action, resource in [("logs.read", "staging/checkout"), ("metrics.read", "staging/checkout")]:
        client.post("/v1/authorize", headers=_auth(), json={"task": "incident-1", "action": action, "resource": resource})
    suggested = client.get("/v1/agents/inc-agent/suggested-lease?tenant=acme", headers=ADMIN).json()["lease"]
    assert suggested["subject"] == "inc-agent" and suggested["task"] == "incident-1"
    assert suggested["actions"] == ["logs.read", "metrics.read"]
    assert suggested["resources"] == ["staging/checkout"]


# --------------------------------------------------------------------------- #
# Observe -> Enforce
# --------------------------------------------------------------------------- #
def test_observe_mode_simulates_instead_of_blocking(client):
    assert client.put("/admin/mode", headers=ADMIN, json={"mode": "observe", "tenant": "acme"}).status_code == 200
    # No lease at all: enforce would deny; observe records and lets through.
    r = client.post("/v1/authorize", headers=_auth(), json={"task": "new-task", "action": "metrics.read",
                                                            "resource": "staging/x"})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "simulate" and body["enforced"] is False
    assert body["would_be"] == "deny" and body["reason"] == "NO_ACTIVE_LEASE"
    assert "Observe mode" in " ".join(body["explanation"])
    agents = client.get("/v1/agents?tenant=acme", headers=ADMIN).json()["agents"]
    assert agents[0]["requested_authority"] == {"metrics.read": 1} and agents[0]["decisions"] == {"simulate": 1}
    # Other tenants stay enforced.
    other = client.post("/v1/authorize", headers=_auth(tenant="other"), json={"task": "t", "action": "metrics.read",
                                                                             "resource": "staging/x"})
    assert other.status_code == 403
    assert client.put("/admin/mode", headers=ADMIN, json={"mode": "enforce", "tenant": "acme"}).status_code == 200
    assert client.post("/v1/authorize", headers=_auth(), json={"task": "new-task", "action": "metrics.read",
                                                               "resource": "staging/x"}).status_code == 403


# --------------------------------------------------------------------------- #
# Quarantine
# --------------------------------------------------------------------------- #
def test_quarantine_holds_every_action(client):
    _lease(client)
    ok = client.post("/v1/authorize", headers=_auth(), json={"task": "incident-1", "action": "logs.read",
                                                             "resource": "staging/checkout"})
    assert ok.status_code == 200
    r = client.post("/v1/agents/inc-agent/quarantine?tenant=acme", headers=ADMIN, json={"note": "drift"})
    assert r.status_code == 200 and r.json()["agent"]["status"] == "quarantined"
    held = client.post("/v1/authorize", headers=_auth(), json={"task": "incident-1", "action": "logs.read",
                                                               "resource": "staging/checkout"})
    assert held.status_code == 423
    assert held.json()["detail"]["decision"] == "quarantine"
    assert held.json()["detail"]["reason"] == "AGENT_QUARANTINED"
    assert client.delete("/v1/agents/inc-agent/quarantine?tenant=acme", headers=ADMIN).status_code == 200
    assert client.post("/v1/authorize", headers=_auth(), json={"task": "incident-1", "action": "logs.read",
                                                               "resource": "staging/checkout"}).status_code == 200


# --------------------------------------------------------------------------- #
# Lineage
# --------------------------------------------------------------------------- #
def test_delegation_records_lineage_and_explains_denial(client):
    _lease(client, actions=["logs.read", "metrics.read", "deployment.read"], resources=["staging/*", "production/*"])
    r = client.post("/v1/leases/lease-inc/delegate", headers=_auth(), json={"agent": "metrics-agent",
                                                                           "actions": ["metrics.read"], "id": "lease-metrics"})
    assert r.status_code == 200
    child = r.json()["lease"]
    assert child["parent_lease"] == "lease-inc" and child["origin"]["kind"] == "parent"
    chain = client.get("/v1/lineage/lease-metrics", headers=ADMIN).json()["lineage"]
    assert [link["subject"] for link in chain] == ["inc-agent", "metrics-agent"]
    assert chain[0]["origin"]["created_by"] == "oncall"

    denied = client.post("/v1/authorize", headers=_auth(agent="metrics-agent"), json={
        "task": "incident-1", "action": "deployment.restart", "resource": "production/checkout"})
    assert denied.status_code == 403
    detail = denied.json()["detail"]
    assert detail["reason"] == "ACTION_NOT_AUTHORIZED"
    assert detail["explanation"][0] == "No authority lineage permits deployment.restart."
    trace = client.get(f"/v1/decisions/{detail['evidence_id']}", headers=ADMIN).json()["trace"]
    assert [ls["subject"] for ls in trace["authority"]["lineage"]] == ["inc-agent", "metrics-agent"]
    assert trace["authority"]["lineage_permits_action"] is False
    agent = client.get("/v1/agents/metrics-agent?tenant=acme", headers=ADMIN).json()
    assert agent["parent_agent"] == "inc-agent"
    parent = client.get("/v1/agents/inc-agent?tenant=acme", headers=ADMIN).json()
    assert parent["children"] == ["metrics-agent"]


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["staging-incident", "github-maintenance", "delegation"])
def test_demo_scenarios_run_through_the_real_engine(client, name):
    scenarios = client.get("/demo/scenarios").json()
    assert name in {s["name"] for s in scenarios["scenarios"]}
    run = client.post(f"/demo/scenarios/{name}/run").json()
    assert run["scenario"] == name
    assert all(step["matches_expected"] for step in run["steps"]), run["steps"]
    outcomes = [s["outcome"] for s in run["steps"]]
    assert outcomes[-1] == "deny" and outcomes[0] == "allow"
    # Every step is a real decision with a readable trace, scoped to the demo tenant.
    for step in run["steps"]:
        trace = client.get(f"/v1/decisions/{step['decision_id']}", headers=DEMO).json()["trace"]
        assert trace["identity"]["tenant"] == "demo" and trace["edge"] == "demo"
        assert trace["task"]["origin"]["text"] == run["prompt"]
    # ALLOW steps executed against the simulated targets and left receipts.
    allowed = [s for s in run["steps"] if s["outcome"] == "allow"]
    assert all(s["executed"] is not None for s in allowed)
    receipts = client.get(f"/v1/decisions/{allowed[-1]['decision_id']}", headers=DEMO).json()["receipts"]
    assert receipts and receipts[0]["reason"] == "UPSTREAM_RESULT_RECEIVED"
    # Demo viewers cannot read other tenants.
    assert client.get("/v1/agents?tenant=acme", headers=DEMO).json()["agents"] == [] or \
        all(a["tenant"] == "demo" for a in client.get("/v1/agents?tenant=acme", headers=DEMO).json()["agents"])
    assert client.get("/v1/system", headers=DEMO).json()["demo"] is True


def test_demo_delegation_lineage_explains_the_denial(client):
    run = client.post("/demo/scenarios/delegation/run").json()
    last = run["steps"][-1]
    assert last["agent"] == "metrics-agent" and last["outcome"] == "deny"
    assert [ls["subject"] for ls in last["lineage"]] == ["incident-agent", "metrics-agent"]
    assert last["explanation"][0] == "No authority lineage permits deployment.restart."


def test_demo_github_consequences_differ(client):
    run = client.post("/demo/scenarios/github-maintenance/run").json()
    stale, main = run["steps"][1], run["steps"][2]
    assert stale["consequence"]["blast_radius"] == 1
    assert main["consequence"]["blast_radius"] > 1 and main["reason"] == "RESOURCE_PROTECTED"
    assert run["targets"]["branches"] == ["main", "feature/latency"]


def test_demo_reset_clears_the_tenant(client):
    client.post("/demo/scenarios/staging-incident/run")
    assert client.get("/v1/agents", headers=DEMO).json()["count"] >= 1
    assert client.post("/demo/reset").json()["reset"] is True
    assert client.get("/v1/agents", headers=DEMO).json()["count"] == 0
    assert client.get("/v1/tasks", headers=DEMO).json()["count"] == 0
