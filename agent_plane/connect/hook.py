"""``agentplane hook`` - the bridge a coding agent calls before, and
optionally after, it acts.

Claude Code and Codex both support a pre-tool hook: the agent hands the hook
a JSON description of the tool it is about to run, and the hook's exit code
decides whether it runs. That is the whole integration.

    stdin   {"tool_name": "Bash", "tool_input": {"command": "git push"}, ...}
    exit 0  proceed
    exit 2  blocked; stderr is shown to the agent and the user

In observe and govern mode the hook always exits 0 and simply records what
happened - nothing a developer connects can break because agent-plane is
watching. Only enforce mode, on an integration that can actually block,
returns 2. If agent-plane is unreachable the hook exits 0 and says so on
stderr: an observability tool must not wedge someone's editor.

``--post`` is a second, optional invocation after the tool actually ran
(Claude Code's PostToolUse hook, wired up by ``agentplane connect``
alongside the pre-tool one). It never blocks anything - it only tells the
server a fact this task proposed was actually confirmed (see
agent_plane.consequence.state), which is what lets a state-conditioned
Enforce-mode denial bind rather than stay advisory. Since PreToolUse and
PostToolUse are separate process invocations, correlating "which decision
does this completion belong to" needs a small local cache keyed by what the
tool call looked like - see ``_signature``. If nothing is cached (the pre
hook never ran, or the cache was cleared), --post is a silent no-op: a
missing completion signal degrades to "stays advisory", never an error.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from agent_plane.connect.credentials import load_credentials

TIMEOUT_SECONDS = 5.0
BLOCK_EXIT = 2
# How many pending pre-hook decisions to remember at once, and for how long -
# a bound on both, so a session that never confirms anything (an unpaired
# PreToolUse, or --post genuinely not firing) can't grow this file forever.
PENDING_CACHE_LIMIT = 200
PENDING_CACHE_MAX_AGE_SECONDS = 3600


def _repo_context(cwd: str | None) -> dict[str, str]:
    """Repository and branch, for turning ``git push`` into a real resource."""
    out: dict[str, str] = {}
    directory = cwd or os.getcwd()
    try:
        remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=directory, timeout=2,
                                capture_output=True, text=True, check=False).stdout.strip()
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=directory, timeout=2,
                                capture_output=True, text=True, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return out
    if remote:
        slug = remote.removesuffix(".git").replace("git@github.com:", "").replace("https://github.com/", "")
        out["repository"] = slug.strip("/")
    if branch and branch != "HEAD":
        out["branch"] = branch
    return out


def _pending_cache_path() -> Path:
    override = os.environ.get("AGENTPLANE_HOME")
    return (Path(override) if override else Path.home() / ".agentplane") / "pending-confirmations.json"


def _signature(event: dict[str, Any]) -> str:
    """What identifies "the same tool call" across the pre and post
    invocations - session (or workspace, if the harness gave no session) plus
    the tool and its arguments, which are identical in both payloads for one
    invocation."""
    key = json.dumps({
        "session": event.get("session") or event.get("workspace_root"),
        "tool": event.get("tool"),
        "arguments": event.get("arguments"),
    }, sort_keys=True, default=str)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def _load_pending() -> dict[str, dict[str, Any]]:
    path = _pending_cache_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _save_pending(pending: dict[str, dict[str, Any]]) -> None:
    now = time.time()
    # Prune by age, then by count, so an unpaired PreToolUse (no matching
    # --post ever arrives) can't grow this file without bound.
    pending = {k: v for k, v in pending.items() if now - v.get("at", 0) < PENDING_CACHE_MAX_AGE_SECONDS}
    if len(pending) > PENDING_CACHE_LIMIT:
        newest = sorted(pending.items(), key=lambda kv: kv[1].get("at", 0), reverse=True)[:PENDING_CACHE_LIMIT]
        pending = dict(newest)
    path = _pending_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pending), encoding="utf-8")


def _payload(raw: dict[str, Any], integration: str) -> dict[str, Any]:
    """Normalize the hook payloads of the agents we support into one event."""
    tool = raw.get("tool_name") or raw.get("tool") or raw.get("name")
    arguments = raw.get("tool_input") or raw.get("input") or raw.get("arguments") or {}
    cwd = raw.get("cwd") or raw.get("workspace") or os.getcwd()
    session = raw.get("session_id") or raw.get("session") or None
    event: dict[str, Any] = {
        "integration": integration,
        "agent": raw.get("agent") or integration,
        "tool": tool,
        "arguments": arguments if isinstance(arguments, dict) else {"input": str(arguments)},
        "workspace_root": cwd,
        "host": raw.get("host") or os.environ.get("HOSTNAME") or Path(cwd).name,
        **_repo_context(cwd),
    }
    if session:
        event["session"] = str(session)
    # A task is the unit authority is granted for. Use whatever the agent knows;
    # fall back to the workspace, which is at least stable across a session.
    event["task"] = str(raw.get("task") or raw.get("transcript_title") or Path(cwd).name or "session")
    if raw.get("prompt") or raw.get("user_prompt"):
        # Sent only as provenance; the server drops the text unless the project
        # explicitly collects prompt content.
        event["origin"] = {"kind": "prompt", "ref": raw.get("prompt_id") or session,
                           "text": raw.get("prompt") or raw.get("user_prompt")}
    return event


def run(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="agentplane hook", description=__doc__)
    parser.add_argument("--integration", default="claude-code")
    parser.add_argument("--url", default=None)
    parser.add_argument("--key", default=None)
    parser.add_argument("--quiet", action="store_true", help="never write to stderr on success")
    parser.add_argument("--post", action="store_true",
                        help="a completion signal (PostToolUse), not a permission check - never blocks")
    args = parser.parse_args(argv)

    try:
        raw = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    creds = load_credentials(args.integration, args.url)
    key = args.key or (creds.key if creds else None)
    url = args.url or (creds.url if creds else None)
    if not key or not url:
        print("agent-plane: not connected (run `agentplane connect claude --key ap_live_...`)", file=sys.stderr)
        return 0

    event = _payload(raw, args.integration)

    if args.post:
        pending = _load_pending()
        entry = pending.pop(_signature(event), None)
        _save_pending(pending)
        if entry is None:
            return 0  # no matching pre-hook decision cached - silent no-op, never an error
        try:
            import httpx

            httpx.post(f"{url.rstrip('/')}/v1/events/action",
                      json={"confirms": entry["decision_id"], "task": entry["task"]},
                      timeout=TIMEOUT_SECONDS, headers={"Authorization": f"Bearer {key}"})
        except Exception:  # noqa: BLE001 - a completion signal must never wedge the developer's agent
            pass
        return 0

    try:
        import httpx

        response = httpx.post(f"{url.rstrip('/')}/v1/events/action", json=event, timeout=TIMEOUT_SECONDS,
                              headers={"Authorization": f"Bearer {key}"})
        decision = response.json() if response.content else {}
    except Exception as exc:  # noqa: BLE001 - never wedge the developer's agent
        print(f"agent-plane: unreachable, not recorded ({type(exc).__name__})", file=sys.stderr)
        return 0

    if response.status_code >= 400 and response.status_code != 403:
        print(f"agent-plane: {decision.get('detail') or response.status_code}", file=sys.stderr)
        return 0

    detail = decision.get("detail") if isinstance(decision.get("detail"), dict) else decision
    if detail.get("evidence_id"):
        # Remembered so a later --post invocation for this same tool call can
        # confirm the fact(s) this decision proposed - see _signature.
        pending = _load_pending()
        pending[_signature(event)] = {"decision_id": detail["evidence_id"], "task": event["task"], "at": time.time()}
        _save_pending(pending)

    outcome = str(detail.get("decision") or "")
    binding = bool(detail.get("binding"))
    explanation = " ".join(detail.get("explanation") or []) or detail.get("reason") or ""

    if outcome in ("deny", "quarantine") and binding:
        print(f"agent-plane blocked this: {explanation}", file=sys.stderr)
        return BLOCK_EXIT
    if outcome == "approval_required" and binding:
        print(f"agent-plane needs a human to approve this first: {explanation}", file=sys.stderr)
        return BLOCK_EXIT
    if outcome in ("deny", "approval_required", "quarantine") and not args.quiet:
        # Govern mode, or a connector that cannot block: say so, do not pretend.
        print(f"agent-plane flagged this ({outcome}): {explanation}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
