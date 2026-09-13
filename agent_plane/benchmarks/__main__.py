"""python -m agent_plane.benchmarks --output benchmark.json"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_plane.benchmarks.runner import ExternalConfig, run_benchmark


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate consequence composition; model baselines require explicit adapters.")
    parser.add_argument("--output", type=Path, default=Path("benchmark-results.json"))
    parser.add_argument("--baselines", type=Path, help="JSON mapping prompt-only/context-engineered to command arrays and labels")
    parser.add_argument("--require-all", action="store_true", help="Fail when any baseline is unrun or has adapter errors")
    args = parser.parse_args(argv)
    external = {name: ExternalConfig.model_validate(value) for name, value in
                (json.loads(args.baselines.read_text(encoding="utf-8")) if args.baselines else {}).items()}
    report = run_benchmark(external=external)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for name, result in report["baselines"].items():
        metrics = result.get("metrics")
        print(f"{name}: {result['status']}" + (f"; {metrics['correct_scenarios']}/{metrics['total_scenarios']} scenarios correct; {metrics['unsafe_allowed']} unsafe allowed; {metrics['benign_blocked']} benign blocked" if metrics else ""))
    print(f"Evidence: {args.output}")
    engine = report["baselines"]["agent-plane"]["metrics"]
    if engine["correct_scenarios"] != report["scenario_count"]:
        return 1
    return 2 if args.require_all and any(b["status"] != "completed" for b in report["baselines"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
