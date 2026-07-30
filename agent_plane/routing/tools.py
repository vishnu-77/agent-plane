"""Tool registry + executor - the agent->tool broker's data plane.

Config-driven (YAML), default-deny: only catalogued tools can be invoked, and the
broker executes them with *its own* credential (from an env var), so the agent
never holds the secret. ``mock`` tools echo their arguments for offline/dev use.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

from agent_plane.config import Settings

_DEFAULT_TOOLS_FILE = "config/tools.yaml"


class ToolExecutionError(Exception):
    """Raised when a catalogued tool fails to execute."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    type: str = "mock"          # "mock" | "http"
    method: str = "POST"
    url: str | None = None
    secret_env: str | None = None   # env var holding the broker's credential
    timeout: float = 30.0


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec]):
        self._specs: dict[str, ToolSpec] = {s.name: s for s in specs}

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def list(self) -> list[str]:
        return list(self._specs)

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        spec = self._specs.get(name)
        if spec is None:
            raise KeyError(name)
        if spec.type == "mock":
            return {"tool": name, "ok": True, "echo": arguments}
        if spec.type == "http":
            headers: dict[str, str] = {}
            if spec.secret_env:
                token = os.environ.get(spec.secret_env)
                if token:
                    headers["Authorization"] = f"Bearer {token}"
            try:
                async with httpx.AsyncClient(timeout=spec.timeout) as client:
                    resp = await client.request(
                        spec.method, spec.url, json=arguments, headers=headers
                    )
                    resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise ToolExecutionError(str(exc)) from exc
            try:
                return resp.json()
            except ValueError:
                return {"status": resp.status_code, "text": resp.text}
        raise ToolExecutionError(f"unknown tool type: {spec.type}")


def load_tool_specs(path: str) -> list[ToolSpec]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    raw = doc.get("tools", [])
    return [
        ToolSpec(
            name=t["name"],
            type=t.get("type", "mock"),
            method=t.get("method", "POST"),
            url=t.get("url"),
            secret_env=t.get("secret_env"),
            timeout=float(t.get("timeout", 30.0)),
        )
        for t in raw
    ]


def build_tool_registry(settings: Settings) -> ToolRegistry:
    path: str | None = settings.tools_file or (
        _DEFAULT_TOOLS_FILE if Path(_DEFAULT_TOOLS_FILE).exists() else None
    )
    if path is None:
        from agent_plane.defaults import default_config_file

        default = default_config_file("tools.yaml")
        path = str(default) if default.exists() else None
    return ToolRegistry(load_tool_specs(path) if path else [])
