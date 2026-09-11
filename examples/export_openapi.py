"""Write the runtime's OpenAPI document to docs/openapi.json.

    python examples/export_openapi.py            # writes docs/openapi.json
    python examples/export_openapi.py --check    # exits 1 if the file is stale

The live equivalent is GET /openapi.json on a running server (Swagger UI at
/docs). Committing the export keeps the API reference reviewable in pull
requests without starting the service.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("docs/openapi.json"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    document = create_app().openapi()
    document["info"]["version"] = "current"  # keep the export stable across releases
    text = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print(f"{args.out} is stale; run: python examples/export_openapi.py", file=sys.stderr)
            sys.exit(1)
        print(f"{args.out} is current")
        return
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out} ({len(document.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
