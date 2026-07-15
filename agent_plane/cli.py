"""``agentplane`` — the packaged entrypoint.

After ``pip install agent-plane`` (or ``pip install .``):

    agentplane init                       # scaffold policies/ and config/ here
    agentplane serve --port 8000          # run the control plane
    agentplane version
    agentplane identity keygen --out-dir keys     # delegation tooling
    agentplane identity issue --key keys/delegation_private.pem --sub alice ...
"""
from __future__ import annotations

import argparse
import shutil
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

    args = parser.parse_args(argv)

    if args.cmd == "init":
        _init(args)
    elif args.cmd == "serve":
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


if __name__ == "__main__":
    main()
