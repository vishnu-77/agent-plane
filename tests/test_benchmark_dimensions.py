"""Phase 31: violation-prevention broken out per dimension (identity/
authority/consequence/task-composition), not just one aggregate rate."""
from agent_plane.benchmarks.runner import run_benchmark, score_by_family
from agent_plane.benchmarks.scenarios import scenarios


def test_score_by_family_groups_correctly():
    cases = scenarios()
    report = run_benchmark(cases=cases)
    by_family = report["baselines"]["agent-plane"]["metrics_by_family"]
    assert "authority-scope-violation" in by_family
    assert "consequence-envelope-violation" in by_family
    assert by_family["authority-scope-violation"]["violation_prevention_rate"] == 1.0
    assert by_family["consequence-envelope-violation"]["violation_prevention_rate"] == 1.0


def test_score_by_family_matches_families_present_in_rows():
    families = {c.family for c in scenarios()}
    report = run_benchmark(cases=scenarios())
    by_family = report["baselines"]["agent-plane"]["metrics_by_family"]
    assert set(by_family) == families


if __name__ == "__main__":
    test_score_by_family_groups_correctly()
    test_score_by_family_matches_families_present_in_rows()
    print("ok")
