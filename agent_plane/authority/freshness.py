"""Threat-model freshness: has the threat model kept up with capability changes?

OWASP AI-TM's core point is that an AI system's threat model goes stale not
just when the architecture changes, but when a capability quietly changes
underneath it. This is the smallest possible enforcement of that: a capability
manifest carries a version, a threat model declares which version it was
written against, and CI fails the moment they drift apart.

Contract:
    capability manifest: {version: "<N>", ...}
    threat model:         {capability_manifest_version: "<N>", ...}
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class FreshnessResult:
    ok: bool
    manifest_version: str | None
    threat_model_manifest_version: str | None
    reason: str


def validate_threat_model_freshness(
    capability_manifest_path: str,
    threat_model_path: str,
) -> FreshnessResult:
    capability = yaml.safe_load(Path(capability_manifest_path).read_text(encoding="utf-8")) or {}
    threat_model = yaml.safe_load(Path(threat_model_path).read_text(encoding="utf-8")) or {}

    manifest_version = capability.get("version")
    threat_version = threat_model.get("capability_manifest_version")

    if manifest_version is None:
        return FreshnessResult(
            ok=False, manifest_version=None,
            threat_model_manifest_version=str(threat_version) if threat_version is not None else None,
            reason="Capability manifest has no version",
        )
    if threat_version is None:
        return FreshnessResult(
            ok=False, manifest_version=str(manifest_version),
            threat_model_manifest_version=None,
            reason="Threat model does not reference a capability manifest version",
        )
    if str(manifest_version) != str(threat_version):
        return FreshnessResult(
            ok=False, manifest_version=str(manifest_version),
            threat_model_manifest_version=str(threat_version),
            reason="Threat model is stale relative to the capability manifest",
        )
    return FreshnessResult(
        ok=True, manifest_version=str(manifest_version),
        threat_model_manifest_version=str(threat_version),
        reason="Threat model matches the current capability manifest version",
    )
