"""The coding-agent connector: what it reports, and when it blocks.

The hook is the only thing standing between a developer's editor and a broken
session, so its failure behaviour matters more than its happy path: it must
never block in observe or govern mode, and never block when agent-plane is
unreachable.
"""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from agent_plane.connect import hook
from agent_plane.connect.credentials import (
    Credentials,
    credentials_path,
    load_credentials,
    save_credentials,
)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTPLANE_HOME", str(tmp_path / "agentplane"))
    monkeypatch.delenv("AGENTPLANE_API_KEY", raising=False)
    monkeypatch.delenv("AGENTPLANE_URL", raising=False)
    return tmp_path


def _run(monkeypatch, payload, response, *, argv=None):
    """Run the hook against a stubbed agent-plane and return (exit, stderr)."""
    sent: dict = {}

    class _Response:
        status_code = response.get("status", 200)
        content = b"{}"

        def json(self):
            return response.get("body", {})

    def fake_post(url, json=None, timeout=None, headers=None):
        sent["url"], sent["json"], sent["headers"] = url, json, headers
        if response.get("raise"):
            raise OSError("connection refused")
        return _Response()

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=fake_post))
    monkeypatch.setattr(hook, "_repo_context", lambda cwd: {"repository": "acme/app", "branch": "main"})
    monkeypatch.setattr("sys.stdin", SimpleNamespace(read=lambda: json.dumps(payload)))
    code = hook.run(argv or ["--integration", "claude-code"])
    return code, sent


# --------------------------------------------------------------------------- #
# credentials
# --------------------------------------------------------------------------- #
def test_credentials_are_stored_outside_anything_committable(home):
    save_credentials(Credentials(url="http://localhost:8000", key="ap_live_secret123456789",
                                 integration="claude-code", project="prj_1"))
    path = credentials_path()
    assert path.is_file() and "agentplane" in str(path)
    # Not in the repository, and not in the editor's settings file.
    assert ".claude" not in str(path) and str(home) in str(path)
    loaded = load_credentials("claude-code")
    assert loaded.key == "ap_live_secret123456789" and loaded.project == "prj_1"
    assert "secret123456789" not in loaded.masked()
    assert load_credentials("codex") is None


def test_status_and_disconnect_need_no_key(home, capsys):
    """Neither reads a credential from the command line, and both must run.

    `disconnect` took a positional named `target`, which is also the name the
    subparser gives the chosen subcommand: the positional overwrote it, so the
    call fell through to the connect path and died looking for --key.
    """
    from agent_plane.connect import cli

    assert cli.main(["status"]) == 1                   # nothing to report
    assert "Not connected" in capsys.readouterr().out

    save_credentials(Credentials(url="http://localhost:8000", key="ap_live_secret123456789",
                                 integration="claude-code", project="prj_1"))
    assert cli.main(["disconnect", "claude"]) == 0
    assert "Disconnected" in capsys.readouterr().out
    assert load_credentials("claude-code") is None

    assert cli.main(["disconnect", "claude"]) == 0      # already gone, still fine
    assert "Nothing to disconnect" in capsys.readouterr().out


def test_environment_beats_the_credentials_file(home, monkeypatch):
    save_credentials(Credentials(url="http://file", key="ap_live_fromfile0000000", integration="claude-code"))
    monkeypatch.setenv("AGENTPLANE_API_KEY", "ap_live_fromenv00000000")
    monkeypatch.setenv("AGENTPLANE_URL", "http://env")
    loaded = load_credentials("claude-code")
    assert loaded.key == "ap_live_fromenv00000000" and loaded.url == "http://env"


# --------------------------------------------------------------------------- #
# payload
# --------------------------------------------------------------------------- #
def test_hook_reports_the_tool_the_agent_is_about_to_run(home, monkeypatch):
    save_credentials(Credentials(url="http://plane", key="ap_live_k0000000000000",
                                 integration="claude-code"))
    _, sent = _run(monkeypatch,
                   {"tool_name": "Bash", "tool_input": {"command": "git push origin main"},
                    "cwd": "/home/dev/app", "session_id": "ses_1"},
                   {"body": {"decision": "simulate", "binding": False, "explanation": []}})
    assert sent["url"] == "http://plane/v1/events/action"
    assert sent["headers"]["Authorization"] == "Bearer ap_live_k0000000000000"
    event = sent["json"]
    assert event["tool"] == "Bash" and event["arguments"]["command"] == "git push origin main"
    assert event["repository"] == "acme/app" and event["branch"] == "main"
    assert event["session"] == "ses_1" and event["integration"] == "claude-code"
    assert event["workspace_root"] == "/home/dev/app"
    # No prompt was given, so none is claimed.
    assert "origin" not in event


def test_hook_sends_prompt_only_as_provenance(home, monkeypatch):
    save_credentials(Credentials(url="http://plane", key="ap_live_k0000000000000", integration="claude-code"))
    _, sent = _run(monkeypatch,
                   {"tool_name": "Edit", "tool_input": {"file_path": "a.ts"},
                    "prompt": "Fix the auth tests", "prompt_id": "p1"},
                   {"body": {"decision": "allow", "binding": True}})
    assert sent["json"]["origin"] == {"kind": "prompt", "ref": "p1", "text": "Fix the auth tests"}


# --------------------------------------------------------------------------- #
# blocking
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("body,expected", [
    ({"decision": "allow", "binding": True}, 0),
    ({"decision": "simulate", "binding": False, "would_be": "deny"}, 0),   # observe never blocks
    ({"decision": "deny", "binding": False}, 0),                            # govern never blocks
    ({"decision": "deny", "binding": True}, hook.BLOCK_EXIT),               # enforce, and it can
    ({"decision": "approval_required", "binding": True}, hook.BLOCK_EXIT),
    ({"decision": "quarantine", "binding": True}, hook.BLOCK_EXIT),
])
def test_hook_blocks_only_when_the_decision_actually_binds(home, monkeypatch, body, expected):
    save_credentials(Credentials(url="http://plane", key="ap_live_k0000000000000", integration="claude-code"))
    code, _ = _run(monkeypatch, {"tool_name": "Bash", "tool_input": {"command": "x"}},
                   {"body": {**body, "explanation": ["because"]}})
    assert code == expected


def test_hook_never_wedges_the_editor(home, monkeypatch, capsys):
    save_credentials(Credentials(url="http://plane", key="ap_live_k0000000000000", integration="claude-code"))
    code, _ = _run(monkeypatch, {"tool_name": "Bash"}, {"raise": True})
    assert code == 0
    assert "unreachable" in capsys.readouterr().err
    # A server error is equally not a reason to stop someone working.
    code, _ = _run(monkeypatch, {"tool_name": "Bash"}, {"status": 500, "body": {"detail": "boom"}})
    assert code == 0


def test_hook_without_credentials_says_so_and_allows(home, monkeypatch, capsys):
    code, _ = _run(monkeypatch, {"tool_name": "Bash"}, {"body": {}})
    assert code == 0 and "not connected" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# installation
# --------------------------------------------------------------------------- #
def test_connect_merges_into_existing_claude_settings(tmp_path):
    from agent_plane.connect.cli import _merge_claude_settings

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "model": "opus",
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "my-own-hook"}]}]},
    }), encoding="utf-8")

    _merge_claude_settings(settings, "agentplane hook --integration claude-code")
    after = json.loads(settings.read_text(encoding="utf-8"))
    assert after["model"] == "opus"                                   # untouched
    commands = [h["command"] for e in after["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert "my-own-hook" in commands and "agentplane hook --integration claude-code" in commands

    # Connecting twice updates in place rather than stacking duplicates.
    _merge_claude_settings(settings, "agentplane hook --integration claude-code --quiet")
    after = json.loads(settings.read_text(encoding="utf-8"))
    commands = [h["command"] for e in after["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert commands.count("agentplane hook --integration claude-code --quiet") == 1
    assert len(commands) == 2
