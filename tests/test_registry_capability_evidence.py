"""PR-7: capability evidence + full C/G/E/D model (capability_outside_authority,
unused_authority), built on the existing declared/requested/exercised/denied
authority fields. Response-only derived values in drift() - nothing persisted."""
from datetime import UTC, datetime

from agent_plane.registry.store import AgentRecord, MemoryRegistry


def test_observe_records_capability_evidence_for_newly_seen_capabilities():
    registry = MemoryRegistry()
    registry.observe(tenant="acme", agent="agt_1", application="app",
                     declared=["repo.read", "tests.execute"], task="t1", action="repo.read",
                     resource="repo/x", outcome="allow", decision_id="dec_1")
    rec = registry.agent("acme", "agt_1")
    assert {e.capability for e in rec.capability_evidence} == {"repo.read", "tests.execute"}
    assert all(e.source == "self_reported" for e in rec.capability_evidence)

    # Re-declaring the same capability doesn't add duplicate evidence.
    registry.observe(tenant="acme", agent="agt_1", application="app",
                     declared=["repo.read"], task="t1", action="repo.read",
                     resource="repo/x", outcome="allow", decision_id="dec_2")
    rec = registry.agent("acme", "agt_1")
    assert len(rec.capability_evidence) == 2


def test_observe_evidence_source_kwarg():
    registry = MemoryRegistry()
    registry.observe(tenant="acme", agent="agt_1", application="app",
                     declared=["deployment.restart"], task="t1", action="deployment.restart",
                     resource="prod/x", outcome="allow", decision_id="dec_1",
                     evidence_source="mcp_tool_discovery")
    rec = registry.agent("acme", "agt_1")
    assert rec.capability_evidence[0].source == "mcp_tool_discovery"


def test_drift_reports_capability_outside_authority_and_unused_authority():
    registry = MemoryRegistry()
    # Declares repo.read and deployment.restart; only repo.read is granted and
    # exercised, deployment.restart is neither granted nor exercised.
    registry.observe(tenant="acme", agent="agt_1", application="app",
                     declared=["repo.read", "deployment.restart"], task="t1", action="repo.read",
                     resource="repo/x", outcome="allow", decision_id="dec_1")
    granted = ["repo.read", "tests.execute"]  # tests.execute granted but never exercised
    drift = registry.drift("acme", "agt_1", granted)
    assert drift["capability_outside_authority"] == ["deployment.restart"]
    assert drift["unused_authority"] == ["tests.execute"]


def test_old_shape_agent_record_still_validates_without_capability_evidence():
    old_shape = {
        "id": "agt_old", "tenant": "acme", "application": "app",
        "status": "active", "first_seen": datetime.now(UTC).isoformat(),
        "last_seen": datetime.now(UTC).isoformat(),
        "declared_capabilities": ["repo.read"],
    }
    rec = AgentRecord.model_validate(old_shape)
    assert rec.capability_evidence == []


if __name__ == "__main__":
    test_observe_records_capability_evidence_for_newly_seen_capabilities()
    test_observe_evidence_source_kwarg()
    test_drift_reports_capability_outside_authority_and_unused_authority()
    test_old_shape_agent_record_still_validates_without_capability_evidence()
    print("ok")
