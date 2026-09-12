"""Require matching runtime/SDK versions, and an exact version tag for publishing."""
from __future__ import annotations

import argparse
import tomllib
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Expected v<version> Git tag")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    runtime = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    sdk = tomllib.loads((root / "sdk/python/pyproject.toml").read_text(encoding="utf-8"))
    version = runtime["project"]["version"]
    if version != sdk["project"]["version"]:
        parser.error("Runtime and SDK package versions must match")
    if args.tag is not None and args.tag != f"v{version}":
        parser.error(f"Release tag must be v{version}, got {args.tag!r}")
    print(version)


if __name__ == "__main__":
    main()
