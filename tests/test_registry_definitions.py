"""PR-6: Agent Definition / Instance split - additive, AgentRecord.id keeping
its existing meaning (per-instance row key), definition_id only ever set
when currently None."""
from agent_plane.registry.store import AgentRecord, MemoryRegistry


def _observe(registry, **overrides):
    kwargs = dict(tenant="acme", agent="agt_1", application="app", declared=["repo.read"],
                  task="t1", action="repo.read", resource="repo/x", outcome="allow",
                  decision_id="dec_1", context={"framework": "claude-code"})
    kwargs.update(overrides)
    return registry.observe(**kwargs)


def test_observe_derives_definition_id_from_framework():
    registry = MemoryRegistry()
    rec = _observe(registry)
    assert rec.definition_id == "claude-code"
    definition = registry.definition("acme", "claude-code")
    assert definition is not None
    assert definition.framework == "claude-code"


def test_observe_never_overwrites_a_manually_set_definition_id():
    registry = MemoryRegistry()
    _observe(registry)
    registry.agent("acme", "agt_1")
    rec = registry.agent("acme", "agt_1")
    rec.definition_id = "operator-assigned"
    registry._put_agent(rec)  # simulate an operator's manual assignment
    _observe(registry, decision_id="dec_2")
    assert registry.agent("acme", "agt_1").definition_id == "operator-assigned"


def test_definitions_list_and_upsert():
    registry = MemoryRegistry()
    _observe(registry)
    assert [d.id for d in registry.definitions("acme")] == ["claude-code"]


def test_old_shape_agent_record_still_validates_without_definition_id():
    """Backward-compat proof: an AgentRecord dict shaped like every row
    stored before PR-6 (no definition_id key) still validates, with
    definition_id defaulting to None."""
    from datetime import UTC, datetime
    old_shape = {
        "id": "agt_old", "tenant": "acme", "application": "app", "framework": "claude-code",
        "status": "active", "first_seen": datetime.now(UTC).isoformat(),
        "last_seen": datetime.now(UTC).isoformat(),
    }
    rec = AgentRecord.model_validate(old_shape)
    assert rec.definition_id is None
    assert rec.capability_evidence == []


if __name__ == "__main__":
    test_observe_derives_definition_id_from_framework()
    test_observe_never_overwrites_a_manually_set_definition_id()
    test_definitions_list_and_upsert()
    test_old_shape_agent_record_still_validates_without_definition_id()
    print("ok")
