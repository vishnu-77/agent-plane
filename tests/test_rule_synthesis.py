from __future__ import annotations

import fnmatch

from agent_plane.consequence.catalog import ActionProfile, ConsequenceCatalog, ResourceProfile
from agent_plane.consequence.envelope import ConsequenceEnvelope
from agent_plane.rules.store import suggest_rule


def event(resource="workspace/a.ts", *, tenant="p", agent="a", decision="d", **profile):
    catalog = ConsequenceCatalog(
        [ResourceProfile(pattern=resource, environment="workspace", criticality="low", **profile)],
        [ActionProfile(pattern="filesystem.read", effect="read", consequence_class="read_only")])
    return {"tenant": tenant, "agent_id": agent, "decision_id": decision, "created_at": "2026-09-13T00:00:00Z",
            "obligations_applied": [{"schema": "agent-plane.trace.v1", "identity": {"tenant": tenant, "agent": agent},
                "task": {"id": "t"}, "action": {"name": "filesystem.read"}, "resource": {"name": resource},
                "decision": {"reason": "ACTION_WITHIN_TASK_AUTHORITY"},
                "consequence": catalog.evaluate("filesystem.read", resource).model_dump(mode="json")}]}


def draft(events):
    return suggest_rule(project_id="p", agent="a", observed={"filesystem.read": 20}, denied={},
                        resources=["workspace/ignored.ts"], evidence=events)


def test_exact_resources_bounds_and_evidence_not_wildcard_generalization():
    result = draft([event()])
    assert result["resources"] == ["workspace/a.ts"]
    assert not fnmatch.fnmatchcase("workspace/secret", result["resources"][0])
    envelope = ConsequenceEnvelope.model_validate(result["permitted_consequence"])
    assert envelope.max_impact == "none" and envelope.environments == ["workspace"]
    assert result["allow"] == ["filesystem.read"]
    assert result["basis"]["evidence"][0]["decision_id"] == "d"
    assert result["requires_review"] is True


def test_unknown_and_cross_tenant_agent_evidence_never_grants_read():
    result = draft([event(tenant="other"), event(agent="other")])
    assert result["allow"] == [] and result["resources"] == []
    assert result["basis"]["unrecorded_actions"] == ["filesystem.read"]
    assert result["permitted_consequence"]["environments"] == []


def test_incomplete_evidence_is_not_reconstructed_from_current_catalog():
    raw = event()
    del raw["obligations_applied"][0]["consequence"]["resource_profile"]
    result = draft([raw])
    assert result["allow"] == []
    assert result["basis"]["eligible_count"] == 0


def test_protected_or_customer_facing_reads_cannot_become_allow_by_frequency():
    result = draft([event(), event("workspace/secret", decision="s", protected=True, customer_facing=True)])
    assert result["allow"] == [] and result["ask"] == ["filesystem.read"]
    assert result["protected_resources"] == ["workspace/secret"]
    assert result["permitted_consequence"]["customer_facing"] is False
    assert result["basis"]["eligible_count"] == 1


def test_literal_glob_characters_do_not_expand_resource_scope():
    name = "workspace/a*[1]?.ts"
    result = draft([event(name)])
    assert fnmatch.fnmatchcase(name, result["resources"][0])
    assert not fnmatch.fnmatchcase("workspace/aaa1x.ts", result["resources"][0])


def test_synthesis_is_order_independent_and_deduplicates_evidence():
    a, b = event(), event("workspace/b.ts", decision="b")
    left, right = draft([a, b, a]), draft([b, a])
    assert left["permitted_consequence"] == right["permitted_consequence"]
    assert left["resources"] == right["resources"]
    assert left["basis"]["eligible_count"] == 2


def test_persistence_bound_is_enforced_including_reachable_effects():
    catalog = ConsequenceCatalog([
        ResourceProfile(pattern="workspace/config", environment="workspace", persistence="transient", dependents=["workspace/store"]),
        ResourceProfile(pattern="workspace/store", environment="workspace", persistence="permanent")],
        [ActionProfile(pattern="config.write", effect="mutate", persistence="transient")])
    consequence = catalog.evaluate("config.write", "workspace/config")
    assert consequence.persistence == "permanent"
    assert any("persistence" in reason for reason in ConsequenceEnvelope(max_persistence="durable").violated_by(consequence))
