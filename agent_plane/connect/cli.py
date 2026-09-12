"""``agentplane connect <target> --key ap_live_...``

One command per integration. Each one verifies the key against the running
service, records the credential outside any file people commit, installs
whatever that agent needs to report, and then tells the truth about what it
can and cannot enforce.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from agent_plane.connect.credentials import (
    DEFAULT_URL,
    Credentials,
    credentials_path,
    forget,
    load_credentials,
    save_credentials,
)

TARGETS = ("claude", "codex", "cursor", "mcp", "sdk")
KIND_OF = {"claude": "claude-code", "codex": "codex", "cursor": "cursor", "mcp": "mcp", "sdk": "custom"}


def _say(*lines: str) -> None:
    for line in lines:
        print(line)


def _handshake(url: str, key: str, integration: str, host: str | None) -> dict[str, Any]:
    import httpx

    response = httpx.post(f"{url.rstrip('/')}/v1/auth/exchange", timeout=10.0,
                          headers={"Authorization": f"Bearer {key}"},
                          json={"integration": integration, "host": host})
    if response.status_code == 401:
        raise SystemExit("That API key was rejected. Copy it again from Integrations in the console.")
    if response.status_code >= 400:
        raise SystemExit(f"agent-plane replied {response.status_code}: {response.text[:200]}")
    return response.json()


def _hook_command(integration: str) -> str:
    executable = "agentplane" if Path(sys.argv[0]).name.startswith("agentplane") else f"{sys.executable} -m agent_plane.cli"
    return f"{executable} hook --integration {integration}"


def _merge_claude_settings(path: Path, command: str) -> None:
    """Add a PreToolUse hook without disturbing anything already configured."""
    settings: dict[str, Any] = {}
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding="utf-8")) or {}
        except ValueError as exc:
            raise SystemExit(f"{path} is not valid JSON; fix or move it first ({exc})") from exc
    hooks = settings.setdefault("hooks", {})
    entries = hooks.setdefault("PreToolUse", [])
    for entry in entries:
        for hook in entry.get("hooks", []):
            if "agentplane hook" in str(hook.get("command", "")) or "agent_plane.cli hook" in str(hook.get("command", "")):
                hook["command"] = command
                break
        else:
            continue
        break
    else:
        entries.append({"matcher": "*", "hooks": [{"type": "command", "command": command}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def _connect_hooked(args: argparse.Namespace, kind: str) -> int:
    session = _handshake(args.url, args.key, kind, args.host)
    save_credentials(Credentials(url=args.url, key=args.key, integration=kind,
                                 project=session["project"]["id"], agent=session["agent"]))
    command = _hook_command(kind)
    scope = "project" if args.scope == "project" else "user"
    if kind == "claude-code":
        target = Path(".claude/settings.json") if scope == "project" else Path.home() / ".claude" / "settings.json"
        _merge_claude_settings(target, command)
        installed = f"pre-tool hook in {target}"
    else:
        target = Path(".codex/hooks.json") if scope == "project" else Path.home() / ".codex" / "hooks.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"pre_tool_use": command}, indent=2) + "\n", encoding="utf-8")
        installed = f"pre-exec hook in {target}"

    project, integration = session["project"], session["integration"]
    _say(
        "",
        f"Connected {integration['kind']} to {project['name']} ({project['id']}).",
        f"  credential   {credentials_path()}",
        f"  installed    {installed}",
        f"  mode         {project['mode'].upper()}",
        f"  observation  {integration['observation']}    enforcement  {integration['enforcement']}",
        "",
    )
    if project["mode"] == "observe":
        _say("Nothing will be blocked. Open the console and watch Activity fill up.", "")
    elif integration["enforcement"] == "partial":
        _say("A tool that runs outside the hook is observed but cannot be stopped.", "")
    return 0


def _connect_mcp(args: argparse.Namespace) -> int:
    if not args.upstream:
        raise SystemExit("Give the MCP server to put agent-plane in front of: --upstream https://.../mcp")
    session = _handshake(args.url, args.key, "mcp", args.host)
    save_credentials(Credentials(url=args.url, key=args.key, integration="mcp",
                                 project=session["project"]["id"]))
    config = Path(args.out or "config/mcp-gateway.yaml")
    _say(
        "",
        f"Connected the MCP gateway to {session['project']['name']}.",
        "",
        "Point your MCP client at agent-plane instead of the upstream server:",
        "",
        json.dumps({"mcpServers": {"agent-plane": {
            "url": f"{args.url.rstrip('/')}/mcp",
            "headers": {"Authorization": f"Bearer {args.key}"}}}}, indent=2),
        "",
        f"Then map the upstream's tools once and set MCP_GATEWAY_FILE={config}:",
        "",
        f"  agentplane mcp discover --upstream {args.upstream} \\",
        f"      --tenant {session['project']['id']} --agent <agent> --task <task> --lease <lease> \\",
        f"      --out {config}",
        "",
        "This is the one integration that is a real chokepoint: agent-plane holds",
        "the upstream credential, so a tool it refuses is never dispatched.",
        "",
    )
    return 0


def _connect_sdk(args: argparse.Namespace) -> int:
    session = _handshake(args.url, args.key, "custom", args.host)
    save_credentials(Credentials(url=args.url, key=args.key, integration="custom",
                                 project=session["project"]["id"]))
    _say(
        "",
        f"Connected to {session['project']['name']} ({session['project']['id']}).",
        "",
        "  export AGENTPLANE_API_KEY=" + args.key,
        f"  export AGENTPLANE_URL={args.url}",
        "",
        "Then, in your agent:",
        "",
        "  from agentplane import AgentPlane",
        "",
        "  ap = AgentPlane()                       # reads the environment",
        '  with ap.task("fix-authentication-tests") as task:',
        '      if task.authorize("filesystem.write", "workspace/src/auth.ts").proceed:',
        "          write_the_file()",
        "",
    )
    return 0


def _status(args: argparse.Namespace) -> int:
    import httpx

    creds = load_credentials(None, None)
    if creds is None:
        _say("Not connected. Run: agentplane connect claude --key ap_live_...")
        return 1
    _say(f"url          {creds.url}", f"integration  {creds.integration}",
         f"key          {creds.masked()}", f"project      {creds.project or 'unknown'}")
    try:
        response = httpx.post(f"{creds.url.rstrip('/')}/v1/auth/exchange", timeout=10.0,
                              headers={"Authorization": f"Bearer {creds.key}"},
                              json={"integration": creds.integration})
        if response.status_code == 200:
            project = response.json()["project"]
            _say(f"mode         {project['mode'].upper()}", "status       connected")
            return 0
        _say(f"status       rejected ({response.status_code})")
    except Exception as exc:  # noqa: BLE001
        _say(f"status       unreachable ({type(exc).__name__})")
    return 1


def _disconnect(args: argparse.Namespace) -> int:
    kind = KIND_OF.get(args.integration, args.integration)
    removed = forget(kind, args.url)
    _say("Disconnected." if removed else "Nothing to disconnect.",
         "Remove the hook entry from your agent's settings file to stop reporting entirely.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentplane connect", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="target", required=True)
    for target in TARGETS:
        cmd = sub.add_parser(target, help=f"connect {target}")
        cmd.add_argument("--key", required=True, help="Project API Key (ap_live_... or ap_test_...)")
        cmd.add_argument("--url", default=os.environ.get("AGENTPLANE_URL", DEFAULT_URL))
        cmd.add_argument("--host", default=os.environ.get("HOSTNAME") or Path.cwd().name)
        cmd.add_argument("--scope", choices=("user", "project"), default="user",
                         help="install the hook for this machine (user) or this repository (project)")
        if target == "mcp":
            cmd.add_argument("--upstream", help="the MCP server to put agent-plane in front of")
            cmd.add_argument("--out", help="where to write the gateway mapping file")
    status = sub.add_parser("status", help="show the current connection")
    status.add_argument("--url", default=os.environ.get("AGENTPLANE_URL", DEFAULT_URL))
    disconnect = sub.add_parser("disconnect", help="forget a stored credential")
    # Not "target": the subparser already owns that name, and a positional of
    # the same name overwrites it, which sent `disconnect claude` down the
    # connect path looking for a --key that this subcommand does not have.
    disconnect.add_argument("integration", choices=TARGETS)
    # No default: disconnecting an integration disconnects it, and --url only
    # narrows that to one control plane.
    disconnect.add_argument("--url", default=None,
                            help="only forget the credential for this control plane")

    args = parser.parse_args(argv)
    if args.target == "status":
        return _status(args)
    if args.target == "disconnect":
        return _disconnect(args)
    if not args.key.startswith(("ap_live_", "ap_test_")):
        raise SystemExit("Use a Project API Key (ap_live_... or ap_test_...) from the Integrations screen.")
    if args.target == "mcp":
        return _connect_mcp(args)
    if args.target == "sdk":
        return _connect_sdk(args)
    return _connect_hooked(args, KIND_OF[args.target])


if __name__ == "__main__":
    raise SystemExit(main())
