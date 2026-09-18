"""Phase 7 (PrincipalRecord persisted in the registry) and Phase 8
(ownership summary: orphaned/unowned/inactive agents, unowned definitions)."""
from datetime import UTC, datetime, timedelta

from agent_plane.registry.store import AgentDefinition, MemoryRegistry


def _observe(registry, **overrides):
    kwargs = dict(tenant="acme", agent="agt_1", application="app", declared=["repo.read"],
                  task="t1", action="repo.read", resource="repo/x", outcome="allow",
                  decision_id="dec_1", context={"framework": "claude-code"})
    kwargs.update(overrides)
    return registry.observe(**kwargs)


def test_observe_with_no_assurance_creates_no_principal():
    """A caller that never populates Actor.assurance (identical to every
    pre-0.8 caller) shouldn't get a principal record at all."""
    registry = MemoryRegistry()
    _observe(registry)
    assert registry.principals("acme") == []


def test_observe_with_assurance_upserts_a_principal():
    registry = MemoryRegistry()
    _observe(registry, assurance="reported", trust_domain="tenant:acme")
    principals = registry.principals("acme")
    assert len(principals) == 1
    assert principals[0].principal_id == "agt_1"
    assert principals[0].assurance == "reported"
    assert principals[0].trust_domain == "tenant:acme"


def test_observe_reflects_most_recent_assurance_not_a_high_water_mark():
    registry = MemoryRegistry()
    _observe(registry, assurance="delegated_verified", trust_domain="tenant:acme", decision_id="d1")
    _observe(registry, assurance="reported", trust_domain="tenant:acme", decision_id="d2")
    assert registry.principal("acme", "agt_1").assurance == "reported"


def test_get_principal_by_id():
    registry = MemoryRegistry()
    _observe(registry, assurance="reported")
    assert registry.principal("acme", "agt_1") is not None
    assert registry.principal("acme", "nonexistent") is None


def test_ownership_summary_flags_orphaned_agent_with_no_framework():
    registry = MemoryRegistry()
    _observe(registry, context={})  # no framework -> no definition_id derived
    summary = registry.ownership_summary("acme")
    assert summary["orphaned_agents"] == ["agt_1"]


def test_ownership_summary_flags_unowned_definition():
    registry = MemoryRegistry()
    _observe(registry)  # derives definition_id="claude-code", owner unset
    summary = registry.ownership_summary("acme")
    assert summary["unowned_agents"] == ["agt_1"]
    assert summary["unowned_definitions"] == ["claude-code"]


def test_ownership_summary_clears_once_definition_is_owned():
    registry = MemoryRegistry()
    _observe(registry)
    registry.upsert_definition(AgentDefinition(id="claude-code", tenant="acme", name="claude-code",
                                               owner="platform-engineering", created_at=datetime.now(UTC)))
    summary = registry.ownership_summary("acme")
    assert summary["unowned_agents"] == []
    assert summary["unowned_definitions"] == []


def test_ownership_summary_flags_inactive_agents():
    registry = MemoryRegistry()
    _observe(registry)
    rec = registry.agent("acme", "agt_1")
    rec.last_seen = datetime.now(UTC) - timedelta(days=30)
    registry._put_agent(rec)
    summary = registry.ownership_summary("acme", inactive_after_days=7)
    assert summary["inactive_agents"] == ["agt_1"]


if __name__ == "__main__":
    test_observe_with_no_assurance_creates_no_principal()
    test_observe_with_assurance_upserts_a_principal()
    test_observe_reflects_most_recent_assurance_not_a_high_water_mark()
    test_get_principal_by_id()
    test_ownership_summary_flags_orphaned_agent_with_no_framework()
    test_ownership_summary_flags_unowned_definition()
    test_ownership_summary_clears_once_definition_is_owned()
    test_ownership_summary_flags_inactive_agents()
    print("ok")
