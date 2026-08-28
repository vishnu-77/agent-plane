"""Operator tooling for the task-authority edge (``agentplane authority ...``).

Examples
--------
Fail CI when the threat model has drifted from the capability manifest::

    python -m agent_plane.authority_cli check-freshness \\
        --capabilities config/capability-manifest.yaml \\
        --threat-model config/threat-model.yaml
"""
from __future__ import annotations

import argparse
import sys

from agent_plane.authority.freshness import validate_threat_model_freshness


def _check_freshness(args: argparse.Namespace) -> None:
    result = validate_threat_model_freshness(args.capabilities, args.threat_model)
    print(f"AI-TM freshness: {'PASS' if result.ok else 'FAIL'}")
    print(f"Capability manifest version: {result.manifest_version}")
    print(f"Threat model's referenced version: {result.threat_model_manifest_version}")
    print(result.reason)
    sys.exit(0 if result.ok else 2)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="authority_cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    freshness = sub.add_parser(
        "check-freshness",
        help="fail if the threat model has drifted from the capability manifest",
    )
    freshness.add_argument("--capabilities", required=True, help="capability manifest YAML")
    freshness.add_argument("--threat-model", required=True, help="threat model metadata YAML")
    freshness.set_defaults(func=_check_freshness)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
