from __future__ import annotations

from pathlib import Path

import yaml

from agent_plane.authority.engine import AuthorityEngine
from agent_plane.authority.schema import AuthorityManifest


def load_authority_manifest(path: str) -> AuthorityManifest:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return AuthorityManifest.model_validate(raw)


def build_authority_engine(path: str | None) -> AuthorityEngine | None:
    if not path:
        return None
    manifest_path = Path(path)
    if not manifest_path.exists():
        return None
    return AuthorityEngine(load_authority_manifest(str(manifest_path)))
