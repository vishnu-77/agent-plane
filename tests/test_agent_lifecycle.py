"""Phase 28: lifecycle governance. agent_lifecycle_state() is derived, not
stored - it can never drift from the records it summarizes because it's
recomputed from them every time, not written and later trusted stale."""
from datetime import UTC, datetime

from agent_plane.registry.store import AgentDefinition, AgentRecord, agent_lifecycle_state


def _rec(**overrides) -> AgentRecord:
    now = datetime.now(UTC)
    base = dict(id="agt_1", tenant="acme", first_seen=now, last_seen=now)
    base.update(overrides)
    return AgentRecord(**base)


def _def(**overrides) -> AgentDefinition:
    base = dict(id="claude-code", tenant="acme", name="claude-code", created_at=datetime.now(UTC))
    base.update(overrides)
    return AgentDefinition(**base)


def test_discovered_when_no_definition_derived_yet():
    assert agent_lifecycle_state(_rec(), None, []) == "discovered"


def test_identified_when_definition_exists_but_unowned():
    assert agent_lifecycle_state(_rec(definition_id="claude-code"), _def(), []) == "identified"


def test_owned_when_definition_has_owner_but_nothing_granted():
    assert agent_lifecycle_state(_rec(definition_id="claude-code"), _def(owner="platform"), []) == "owned"


def test_authorised_when_granted_but_never_exercised():
    rec = _rec(definition_id="claude-code")
    assert agent_lifecycle_state(rec, _def(owner="platform"), ["repo.read"]) == "authorised"


def test_active_when_exercised():
    rec = _rec(definition_id="claude-code", exercised_authority={"repo.read": 3})
    assert agent_lifecycle_state(rec, _def(owner="platform"), ["repo.read"]) == "active"


def test_suspended_overrides_everything_except_revoked():
    rec = _rec(definition_id="claude-code", status="quarantined", exercised_authority={"repo.read": 3})
    assert agent_lifecycle_state(rec, _def(owner="platform"), ["repo.read"]) == "suspended"


def test_revoked_is_terminal_and_overrides_suspended():
    rec = _rec(status="quarantined", lifecycle_revoked=True)
    assert agent_lifecycle_state(rec, _def(owner="platform"), ["repo.read"]) == "revoked"


if __name__ == "__main__":
    test_discovered_when_no_definition_derived_yet()
    test_identified_when_definition_exists_but_unowned()
    test_owned_when_definition_has_owner_but_nothing_granted()
    test_authorised_when_granted_but_never_exercised()
    test_active_when_exercised()
    test_suspended_overrides_everything_except_revoked()
    test_revoked_is_terminal_and_overrides_suspended()
    print("ok")
