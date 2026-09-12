"""``agentplane`` - the packaged entrypoint.

After ``pip install agent-plane`` (or ``pip install .``):

    agentplane serve --port 8000               # run the control plane
    agentplane connect claude --key ap_live_…  # connect a coding agent
    agentplane connect status
    agentplane rules check permissions.yaml    # permissions as a file
    agentplane init                            # scaffold policies/ and config/ here
    agentplane version
    agentplane identity keygen --out-dir keys     # delegation tooling
    agentplane identity issue --key keys/delegation_private.pem --sub alice ...
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _init(args: argparse.Namespace) -> None:
    from agent_plane.defaults import defaults_root

    target = Path(args.dir)
    src = defaults_root()
    copied, skipped = 0, 0
    for sub in ("policies", "config"):
        dst_dir = target / sub
        dst_dir.mkdir(parents=True, exist_ok=True)
        for f in sorted((src / sub).glob("*.yaml")):
            dst = dst_dir / f.name
            if dst.exists() and not args.force:
                skipped += 1
                continue
            shutil.copyfile(f, dst)
            copied += 1
    print(f"scaffolded {copied} file(s) into {target.resolve()} ({skipped} skipped)")
    print("Edit policies/*.yaml and config/*.yaml, then: agentplane serve")


def main(argv: list[str] | None = None) -> None:
    # `connect` and `hook` own their own flags (including --help), so hand the
    # rest of the command line straight to them before argparse claims it.
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "connect":
        from agent_plane.connect import cli as connect_cli

        raise SystemExit(connect_cli.main(raw[1:]))
    if raw and raw[0] == "rules":
        from agent_plane.rules import cli as rules_cli

        raise SystemExit(rules_cli.main(raw[1:]))
    if raw and raw[0] == "hook":
        from agent_plane.connect import hook as hook_cli

        raise SystemExit(hook_cli.run(raw[1:]))

    parser = argparse.ArgumentParser(prog="agentplane")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="run the control plane (uvicorn)")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument("--workers", type=int, default=1)

    sub.add_parser("version", help="print the installed version")

    init = sub.add_parser("init", help="scaffold default policies/ and config/ here")
    init.add_argument("--dir", default=".", help="target directory (default: .)")
    init.add_argument("--force", action="store_true", help="overwrite existing files")

    ident = sub.add_parser("identity", help="manage delegation credentials")
    ident.add_argument("args", nargs=argparse.REMAINDER)

    authority = sub.add_parser("authority", help="task-authority tooling (e.g. threat-model freshness)")
    authority.add_argument("args", nargs=argparse.REMAINDER)

    mcp = sub.add_parser("mcp", help="MCP gateway tooling (discover -> mapping YAML)")
    mcp.add_argument("args", nargs=argparse.REMAINDER)

    connect = sub.add_parser("connect", add_help=False,
                             help="connect an agent: claude, codex, cursor, mcp, sdk")
    connect.add_argument("args", nargs=argparse.REMAINDER)

    hook = sub.add_parser("hook", add_help=False,
                          help="pre-tool hook called by a connected coding agent")
    hook.add_argument("args", nargs=argparse.REMAINDER)

    rules = sub.add_parser("rules", add_help=False,
                           help="permissions as a file: check, pull, push")
    rules.add_argument("args", nargs=argparse.REMAINDER)

    args = parser.parse_args(argv)

    if args.cmd == "init":
        _init(args)
    elif args.cmd == "serve":
        if args.workers < 1:
            parser.error("--workers must be at least 1")
        if args.workers != 1:
            from agent_plane.config import get_settings

            if get_settings().authority_store == "memory":
                parser.error("AUTHORITY_STORE=memory is process-local; --workers must be 1")
        import uvicorn

        uvicorn.run(
            "agent_plane.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            workers=args.workers if not args.reload else 1,
        )
    elif args.cmd == "version":
        from importlib.metadata import PackageNotFoundError, version

        try:
            print("agent-plane", version("agent-plane"))
        except PackageNotFoundError:  # running from source without install
            print("agent-plane (dev)")
    elif args.cmd == "identity":
        from agent_plane import identity_cli

        identity_cli.main(args.args)
    elif args.cmd == "authority":
        from agent_plane import authority_cli

        authority_cli.main(args.args)
    elif args.cmd == "mcp":
        from agent_plane.enforcement import discover

        discover.main(args.args)
    elif args.cmd == "connect":
        from agent_plane.connect import cli as connect_cli

        raise SystemExit(connect_cli.main(args.args))
    elif args.cmd == "hook":
        from agent_plane.connect import hook as hook_cli

        raise SystemExit(hook_cli.run(args.args))
    elif args.cmd == "rules":
        from agent_plane.rules import cli as rules_cli

        raise SystemExit(rules_cli.main(args.args))


if __name__ == "__main__":
    main()
