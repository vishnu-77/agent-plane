from __future__ import annotations

import argparse
import sys

from agent_plane.authority.validator import validate_threat_model_freshness


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m agent_plane.authority",
        description="Validate AI-TM capability/threat-model invariants.",
    )
    parser.add_argument("--capabilities", required=True, help="capability manifest YAML")
    parser.add_argument("--threat-model", required=True, help="threat model metadata YAML")
    args = parser.parse_args()

    result = validate_threat_model_freshness(args.capabilities, args.threat_model)
    print(f"AI-TM freshness: {'PASS' if result.ok else 'FAIL'}")
    print(f"Capability manifest: {result.manifest_version}")
    print(f"Threat model manifest: {result.threat_model_manifest_version}")
    print(result.reason)
    sys.exit(0 if result.ok else 2)


if __name__ == "__main__":
    main()
