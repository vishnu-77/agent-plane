"""``agentplane rules`` - permissions as a file, from the command line.

    agentplane rules pull --project prj_... --key ap_mgmt_... > permissions.yaml
    agentplane rules push permissions.yaml --project prj_... --key ap_mgmt_...
    agentplane rules check permissions.yaml

``check`` needs no credential and touches nothing: it is what a pull request
runs to find out whether the file is readable before anyone applies it.

``push`` merges by rule name. ``--replace`` makes the file the whole truth for
the project and deletes the rules it does not mention, which is what a pipeline
usually wants and is never the default.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from agent_plane.rules.yaml_io import RulesYamlError, load_rules

DEFAULT_URL = "http://127.0.0.1:8000"


def _request(url: str, key: str, *, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("X-Admin-Token", key)      # a management key or ADMIN_TOKEN
    request.add_header("Authorization", f"Bearer {key}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:   # noqa: S310 - operator-supplied URL
        return json.loads(response.read() or "{}")


def _fail(message: str) -> int:
    print(f"agentplane rules: {message}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentplane rules", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    check = sub.add_parser("check", help="validate a permissions file; no credential needed")
    check.add_argument("file")

    pull = sub.add_parser("pull", help="write a project's permissions to stdout")
    push = sub.add_parser("push", help="apply a permissions file to a project")
    push.add_argument("file")
    push.add_argument("--replace", action="store_true",
                      help="delete rules the file does not mention")
    for p in (pull, push):
        p.add_argument("--project", required=True)
        p.add_argument("--key", default=os.environ.get("AGENTPLANE_MGMT_KEY"),
                       help="management key (ap_mgmt_...) or ADMIN_TOKEN; "
                            "defaults to AGENTPLANE_MGMT_KEY")
        p.add_argument("--url", default=os.environ.get("AGENTPLANE_URL", DEFAULT_URL))

    args = parser.parse_args(argv)

    if args.cmd == "check":
        try:
            rules = load_rules(Path(args.file).read_text(encoding="utf-8"))
        except OSError as exc:
            return _fail(str(exc))
        except RulesYamlError as exc:
            return _fail(str(exc))
        print(f"{args.file}: {len(rules)} rule(s), readable")
        for rule in rules:
            print(f"  {rule['name']}: {len(rule.get('allow', []))} allowed, "
                  f"{len(rule.get('ask', []))} ask first, {len(rule.get('never', []))} never")
        return 0

    if not args.key:
        return _fail("no credential: pass --key or set AGENTPLANE_MGMT_KEY")

    try:
        if args.cmd == "pull":
            body = _request(f"{args.url}/v1/rules/export?project={args.project}", args.key)
            sys.stdout.write(body["yaml"])
            return 0

        text = Path(args.file).read_text(encoding="utf-8")
        load_rules(text)          # fail here, before anything reaches the server
        result = _request(f"{args.url}/v1/rules/import", args.key, method="POST",
                          body={"project": args.project, "yaml": text,
                                "mode": "replace" if args.replace else "merge"})
        print(f"{len(result['created'])} created, {len(result['updated'])} updated, "
              f"{len(result['deleted'])} removed")
        return 0
    except RulesYamlError as exc:
        return _fail(str(exc))
    except OSError as exc:
        if isinstance(exc, urllib.error.HTTPError):
            detail = exc.read().decode(errors="replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except ValueError:
                pass
            return _fail(f"{exc.code}: {detail}")
        return _fail(f"cannot reach {args.url} ({exc.__class__.__name__})")


if __name__ == "__main__":
    raise SystemExit(main())
