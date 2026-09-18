"""Phase 22: enforcement coverage, reusing INTEGRATION_CATALOG's already-
curated per-connector tiers rather than a new speculative taxonomy."""
from agent_plane.registry.store import MemoryRegistry


def _observe(registry, agent, framework):
    registry.observe(tenant="acme", agent=agent, application="app", declared=["repo.read"],
                     task="t1", action="repo.read", resource="repo/x", outcome="allow",
                     decision_id=f"dec_{agent}", context={"framework": framework})


def test_coverage_reflects_integration_catalog_tiers():
    registry = MemoryRegistry()
    _observe(registry, "agt_mcp", "mcp")             # full
    _observe(registry, "agt_claude", "claude-code")   # partial
    _observe(registry, "agt_cursor", "cursor")        # advisory
    coverage = registry.enforcement_coverage("acme")
    assert coverage["agents"] == 3
    assert coverage["by_enforcement_tier"] == {"full": 1, "partial": 1, "advisory": 1}
    by_agent = {row["agent"]: row for row in coverage["per_agent"]}
    assert by_agent["agt_mcp"]["enforcement"] == "full"
    assert by_agent["agt_claude"]["enforcement"] == "partial"
    assert by_agent["agt_cursor"]["enforcement"] == "advisory"


def test_unknown_framework_falls_back_to_custom_advisory():
    registry = MemoryRegistry()
    _observe(registry, "agt_x", "some-unlisted-framework")
    coverage = registry.enforcement_coverage("acme")
    assert coverage["by_enforcement_tier"] == {"advisory": 1}


if __name__ == "__main__":
    test_coverage_reflects_integration_catalog_tiers()
    test_unknown_framework_falls_back_to_custom_advisory()
    print("ok")
