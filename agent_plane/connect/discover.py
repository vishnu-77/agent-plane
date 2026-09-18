"""Environment auto-discovery (Phase 16): `agentplane connect` scans the
current environment for installed integrations instead of requiring a
developer to already know which `connect <target>` to run. Filesystem
probes only - no network calls, no credentials needed to discover.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiscoveredIntegration:
    target: str          # matches connect/cli.py's TARGETS
    label: str
    evidence: str         # what was found, for a human to verify the guess


def discover(cwd: Path | None = None, home: Path | None = None) -> list[DiscoveredIntegration]:
    cwd = cwd or Path.cwd()
    home = home or Path.home()
    found: list[DiscoveredIntegration] = []

    claude_settings = [cwd / ".claude" / "settings.json", cwd / ".claude" / "settings.local.json",
                       home / ".claude" / "settings.json"]
    hit = next((p for p in claude_settings if p.exists()), None)
    if hit is not None:
        found.append(DiscoveredIntegration("claude", "Claude Code", str(hit)))

    codex_dirs = [cwd / ".codex", home / ".codex"]
    hit = next((p for p in codex_dirs if p.exists()), None)
    if hit is not None:
        found.append(DiscoveredIntegration("codex", "Codex", str(hit)))

    cursor_dirs = [cwd / ".cursor", home / ".cursor"]
    hit = next((p for p in cursor_dirs if p.exists()), None)
    if hit is not None:
        found.append(DiscoveredIntegration("cursor", "Cursor / OpenCode", str(hit)))

    mcp_config_candidates = [cwd / "mcp.json", cwd / ".mcp.json", cwd / "config" / "mcp-gateway.yaml"]
    hit = next((p for p in mcp_config_candidates if p.exists()), None)
    if hit is not None:
        found.append(DiscoveredIntegration("mcp", "MCP server", str(hit)))

    return found
