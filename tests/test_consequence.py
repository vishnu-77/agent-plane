"""Consequence modelling and the consequence boundary on decisions."""
from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient

from agent_plane.authority.lease import AuthorityLease, lease_attenuation_errors
from agent_plane.authority.service import consequence_violations
from agent_plane.consequence.catalog import build_consequence_catalog

JWT_SECRET = "consequence-secret"
ADMIN = {"X-Admin-Token": "test-admin"}


@pytest.fixture()
def catalog():
    return build_consequence_catalog(type("S", (), {"resources_file": None})())


def test_same_verb_different_consequence(catalog):
    staging = catalog.evaluate("deployment.restart", "staging/checkout")
    production = catalog.evaluate("deployment.restart", "production/checkout")
    assert staging.effect == production.effect == "restart"
    assert staging.impact == "medium" and staging.environment == "staging" and not staging.customer_facing
    assert production.impact == "critical" and production.customer_facing
    assert "production/payments" in production.downstream
    assert production.blast_radius >= 3
    assert "customer-facing workload" in production.summary


def test_reads_do_not_change_state(catalog):
    c = catalog.evaluate("logs.read", "production/checkout")
    assert not c.mutating and c.impact in ("none", "low") and c.blast_radius == 0 and c.downstream == []


def test_main_branch_vs_stale_branch(catalog):
    stale = catalog.evaluate("branch.delete", "github://demo/agent-plane/branches/stale-feature")
    main = catalog.evaluate("branch.delete", "github://demo/agent-plane/branches/main")
    assert stale.impact in ("low", "medium") and stale.downstream == []
    assert main.protected and main.impact in ("high", "critical")
    assert any(d.startswith("ci://") for d in main.downstream)
    assert any(d.startswith("production") for d in main.downstream)


def test_impact_reflects_the_worst_reachable_resource_not_just_the_root():
    """The root resource an action targets may look mild on its own while
    something it reaches downstream is not - a low-criticality, internal-only
    config resource whose only dependent is customer-facing and irreversible
    must report *that*, not its own mild rating."""
    from agent_plane.consequence.catalog import ActionProfile, ConsequenceCatalog, ResourceProfile

    catalog = ConsequenceCatalog(
        resources=[
            ResourceProfile(pattern="internal/config", environment="production", criticality="low",
                            customer_facing=False, reversibility="reversible", dependents=["internal/billing"]),
            ResourceProfile(pattern="internal/billing", environment="production", criticality="critical",
                            customer_facing=True, reversibility="irreversible"),
        ],
        actions=[ActionProfile(pattern="config.write", effect="mutate")],
    )
    c = catalog.evaluate("config.write", "internal/config")
    assert c.customer_facing is True
    assert c.reversibility == "irreversible"
    assert c.impact in ("high", "critical")
    assert "internal/billing" in c.downstream


def test_unknown_resources_get_inferred_profiles(catalog):
    c = catalog.evaluate("thing.delete", "prod-db/users")
    assert c.environment == "production" and c.effect == "delete" and c.impact == "high"
    r = catalog.evaluate("thing.get", "sandbox/x")
    assert not r.mutating


def test_permitted_consequence_violations(catalog):
    lease = AuthorityLease(id="l", task="t", subject="a", resources=["*"], actions=["deployment.restart"],
                           permitted_consequence={"environments": ["staging"], "customer_facing": False,
                                                  "max_impact": "medium"})
    assert consequence_violations(lease, catalog.evaluate("deployment.restart", "staging/checkout")) == []
    violations = consequence_violations(lease, catalog.evaluate("deployment.restart", "production/checkout"))
    assert any("production" in v for v in violations)
    assert any("customer-facing" in v for v in violations)
    assert any("critical" in v for v in violations)
    # A reversible-only lease may never cause an irreversible effect.
    lease2 = AuthorityLease(id="l2", task="t", subject="a", resources=["*"], actions=["email.send"],
                            maximum_impact="reversible")
    assert consequence_violations(lease2, catalog.evaluate("email.send", "email/customer"))


def test_sensitive_reads_are_checked_against_the_envelope_too():
    """A read is not exempt from a lease's permitted_consequence just because
    it doesn't mutate anything - credentials.read on a production secret is
    exactly the kind of "technically a read" action the envelope exists to
    bound."""
    from agent_plane.consequence.catalog import ActionProfile, ConsequenceCatalog, ResourceProfile

    catalog = ConsequenceCatalog(
        resources=[ResourceProfile(pattern="production/*", environment="production", criticality="critical")],
        actions=[ActionProfile(pattern="credentials.read", effect="read", consequence_class="credential_access")],
    )
    consequence = catalog.evaluate("credentials.read", "production/db-password")
    assert not consequence.mutating  # it's a read
    assert consequence.impact not in ("none", "low")  # but a sensitive one

    lease = AuthorityLease(id="l", task="t", subject="a", resources=["*"], actions=["credentials.read"],
                           permitted_consequence={"environments": ["staging"]})
    violations = consequence_violations(lease, consequence)
    assert violations  # today: silently [] because the action doesn't mutate anything

    # A read that's genuinely low-impact still passes every bound trivially -
    # nothing here should start denying ordinary reads (test_reads_do_not_change_state
    # already covers that reads compute impact "none"/"low" in the first place).


def test_permitted_consequence_only_attenuates():
    parent = AuthorityLease(id="p", task="t", subject="a", resources=["*"], actions=["x.do"],
                            permitted_consequence={"max_impact": "medium", "environments": ["staging"],
                                                   "customer_facing": False})
    ok = AuthorityLease(id="c", task="t", subject="b", resources=["*"], actions=["x.do"],
                        permitted_consequence={"max_impact": "low", "environments": ["staging"],
                                               "customer_facing": False})
    assert lease_attenuation_errors(parent, ok) == []
    wider = AuthorityLease(id="c2", task="t", subject="b", resources=["*"], actions=["x.do"],
                           permitted_consequence={"max_impact": "high", "environments": ["staging", "production"]})
    errors = lease_attenuation_errors(parent, wider)
    assert any("max_impact" in e for e in errors) and any("environments" in e for e in errors)
    dropped = AuthorityLease(id="c3", task="t", subject="b", resources=["*"], actions=["x.do"])
    assert lease_attenuation_errors(parent, dropped)  # silently dropping every bound widens


def test_compiled_rules_narrow_string_valued_consequence_bounds():
    """Two rules bounding max_impact/max_reversibility must narrow, not
    "first rule wins" - these are strings, which a naive list/bool/int
    per-key merge doesn't touch at all."""
    from datetime import UTC, datetime

    from agent_plane.rules.store import AuthorityRule, compile_rules

    now = datetime.now(UTC)
    medium = AuthorityRule(id="r1", project_id="p", name="medium", allow=["x.do"],
                           permitted_consequence={"max_impact": "medium", "max_reversibility": "recoverable"},
                           created_at=now, updated_at=now)
    high = AuthorityRule(id="r2", project_id="p", name="high", allow=["x.do"],
                         permitted_consequence={"max_impact": "high", "max_reversibility": "irreversible"},
                         created_at=now, updated_at=now)
    # Order must not matter: whichever order they're evaluated in, the
    # compiled lease ends up with the narrower (medium/recoverable) bound.
    for rules in ([medium, high], [high, medium]):
        lease = compile_rules(rules, project_id="p", agent="a", task="t")
        assert lease.permitted_consequence["max_impact"] == "medium"
        assert lease.permitted_consequence["max_reversibility"] == "recoverable"


# --------------------------------------------------------------------------- #
# Through the API
# --------------------------------------------------------------------------- #
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


def _auth(agent="ops-agent", tenant="acme"):
    tok = jwt.encode({"sub": "u1", "tenant": tenant, "agent_id": agent, "allowed_tools": ["deployment", "email"]},
                     JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


def test_consequence_boundary_denies_in_scope_action(client):
    # Scope covers production, but the task may only cause staging consequences.
    r = client.post("/v1/leases", headers=ADMIN, json={
        "id": "lease-cons", "subject": "ops-agent", "tenant": "acme", "task": "fix",
        "resources": ["staging/*", "production/*"], "actions": ["deployment.restart"],
        "permitted_consequence": {"environments": ["staging"]}})
    assert r.status_code == 200
    ok = client.post("/v1/authorize", headers=_auth(), json={"task": "fix", "action": "deployment.restart",
                                                             "resource": "staging/checkout"})
    assert ok.status_code == 200 and ok.json()["consequence"]["environment"] == "staging"
    assert ok.json()["explanation"]
    bad = client.post("/v1/authorize", headers=_auth(), json={"task": "fix", "action": "deployment.restart",
                                                              "resource": "production/checkout"})
    assert bad.status_code == 403
    detail = bad.json()["detail"]
    assert detail["reason"] == "CONSEQUENCE_OUTSIDE_TASK_BOUNDARY"
    assert detail["consequence"]["customer_facing"] is True
    assert "exceeds the task boundary" in " ".join(detail["explanation"])
    # The denial did not spend a use.
    items = client.get("/admin/leases", headers=ADMIN).json()["items"]
    assert next(i for i in items if i["id"] == "lease-cons")["uses"]["deployment.restart"] == 1


def test_decision_trace_is_readable(client):
    client.post("/v1/leases", headers=ADMIN, json={
        "id": "lease-trace", "subject": "ops-agent", "tenant": "acme", "task": "fix",
        "resources": ["staging/*"], "actions": ["deployment.restart"],
        "origin": {"kind": "human", "created_by": "alice", "text": "restart staging checkout"}})
    r = client.post("/v1/authorize", headers=_auth(), json={
        "task": "fix", "action": "deployment.restart", "resource": "production/checkout",
        "context": {"conversation_id": "conv-1", "origin": "human"}})
    assert r.status_code == 403
    evidence = r.json()["detail"]["evidence_id"]
    trace = client.get(f"/v1/decisions/{evidence}", headers=ADMIN).json()["trace"]
    assert trace["schema"] == "agent-plane.trace.v1"
    assert trace["identity"]["agent"] == "ops-agent"
    assert trace["resource"]["scope"] == "outside"
    assert trace["decision"]["outcome"] == "deny"
    assert trace["decision"]["reason"] == "RESOURCE_OUTSIDE_DELEGATED_SCOPE"
    assert trace["consequence"]["environment"] == "production"
    assert trace["context"]["conversation_id"] == "conv-1"
    assert "No authority lineage permits that consequence." in trace["explanation"]
    # The lineage shows the lease the agent does hold for this task.
    assert trace["authority"]["lineage"][0]["origin"]["created_by"] == "alice"
    listing = client.get("/v1/decisions?tenant=acme", headers=ADMIN).json()["decisions"]
    assert listing[0]["decision_id"] == evidence and listing[0]["outcome"] == "deny"
