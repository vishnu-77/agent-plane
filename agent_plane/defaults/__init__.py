"""Bundled default policies and config.

Used when the working directory has none - so ``pip install agent-plane`` then
``agentplane serve`` works out of the box with sane, *non* allow-all defaults.
``agentplane init`` copies these into the current directory for customization.
"""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def defaults_root() -> Path:
    return Path(str(files("agent_plane.defaults")))


def default_policies_dir() -> Path:
    return defaults_root() / "policies"


def default_config_file(name: str) -> Path:
    return defaults_root() / "config" / name
