from __future__ import annotations

import json
import sys

from agent_plane.benchmarks.runner import ExternalConfig, baseline_input, run_benchmark, score
from agent_plane.benchmarks.scenarios import scenarios


def test_composition_suite_and_real_stateless_ablation():
    report = run_benchmark()
    stateful = report["baselines"]["agent-plane"]["metrics"]
    stateless = report["baselines"]["stateless-harness"]["metrics"]
    assert report["scenario_count"] == 27
    assert stateful["correct_scenarios"] == 27
    assert stateful["unsafe_allowed"] == 0 and stateful["benign_blocked"] == 0
    assert stateless["unsafe_allowed"] == 4 and stateless["benign_blocked"] == 0
    assert report["baselines"]["prompt-only"]["metrics"] is None
    assert report["baselines"]["context-engineered"]["status"] == "not_run"


def test_external_baselines_get_no_labels_or_future_and_context_is_explicit():
    case = scenarios()[0]
    prompt = baseline_input(case, case.steps[0], [], "prompt-only")
    contextual = baseline_input(case, case.steps[0], [], "context-engineered")
    assert "history" not in prompt and "catalog" not in prompt
    assert "history" in contextual and "catalog" in contextual
    for payload in [prompt, contextual]:
        assert "expected" not in json.dumps(payload)
        assert "completed" not in payload["current_action"]
        assert case.id not in json.dumps(payload)


def test_external_process_protocol_scores_actual_responses(tmp_path):
    adapter = tmp_path / "baseline.py"
    adapter.write_text('import json,sys\njson.load(sys.stdin)\nprint(json.dumps({"decision":"allow","reason":"test policy","model":"fixture-not-a-model","input_tokens":4,"output_tokens":2,"tool_calls":0}))\n')
    report = run_benchmark(cases=scenarios()[:1], external={"prompt-only": ExternalConfig(command=[sys.executable, str(adapter)], label="Protocol test fixture")})
    result = report["baselines"]["prompt-only"]
    assert result["status"] == "completed"
    assert result["metrics"]["unsafe_allowed"] == 1
    assert result["metrics"]["reported_usage"]["input_tokens"] == 8
    assert str(adapter) not in json.dumps(report)  # command arguments can contain credentials


def test_bad_external_output_is_an_error_not_a_successful_block(tmp_path):
    adapter = tmp_path / "bad.py"
    adapter.write_text('print("not JSON")\n')
    report = run_benchmark(cases=scenarios()[:1], external={"context-engineered": ExternalConfig(command=[sys.executable, str(adapter)], label="Invalid fixture")})
    result = report["baselines"]["context-engineered"]
    assert result["status"] == "completed_with_errors"
    assert result["metrics"]["errors"] == 1
    assert result["metrics"]["violation_prevention_rate"] is None
    assert result["cases"][0]["skipped_steps"] == 1


def test_zero_denominators_are_unknown_not_perfect_scores():
    metrics = score([])
    assert metrics["violation_prevention_rate"] is None
    assert metrics["false_positive_rate"] is None
    assert metrics["reported_usage"]["input_tokens"] is None
