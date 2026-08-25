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
    """Fail when the threat model is not tied to the current capability manifest.

    PoC contract:
      capability manifest: {version: "N", ...}
      threat model: {capability_manifest_version: "N", ...}
    """
    capability = yaml.safe_load(Path(capability_manifest_path).read_text(encoding="utf-8")) or {}
    threat_model = yaml.safe_load(Path(threat_model_path).read_text(encoding="utf-8")) or {}

    manifest_version = capability.get("version")
    threat_version = threat_model.get("capability_manifest_version")

    if manifest_version is None:
        return FreshnessResult(
            ok=False,
            manifest_version=None,
            threat_model_manifest_version=str(threat_version) if threat_version is not None else None,
            reason="Capability manifest has no version",
        )
    if threat_version is None:
        return FreshnessResult(
            ok=False,
            manifest_version=str(manifest_version),
            threat_model_manifest_version=None,
            reason="Threat model does not reference a capability manifest version",
        )
    if str(manifest_version) != str(threat_version):
        return FreshnessResult(
            ok=False,
            manifest_version=str(manifest_version),
            threat_model_manifest_version=str(threat_version),
            reason="Threat model is stale relative to the capability manifest",
        )

    return FreshnessResult(
        ok=True,
        manifest_version=str(manifest_version),
        threat_model_manifest_version=str(threat_version),
        reason="Threat model references the current capability manifest",
    )
