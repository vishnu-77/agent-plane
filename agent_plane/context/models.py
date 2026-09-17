"""Context-plane records.

Context is influence, not authority. These records describe things that may
shape an agent (instructions, skills, tools, MCP, RAG, memory, harnesses) and
preserve immutable fingerprints so drift can be explained later. Nothing in
this module grants an action or changes an authority decision.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ContextKind = Literal[
    "instruction", "skill", "tool", "mcp", "knowledge", "memory",
    "harness", "event", "external", "other",
]
TrustLevel = Literal["verified", "project-bound", "repository", "internal", "external", "unknown"]
InfluenceLevel = Literal["low", "medium", "high"]


def utcnow() -> datetime:
    return datetime.now(UTC)


class ContextRiskVector(BaseModel):
    """Multidimensional posture, never an enforcement decision by itself.

    Values are normalized 0..1. ``exposure_score`` is derived only so the UI
    can sort attention; ALLOW/DENY/APPROVAL/QUARANTINE remain authority and
    consequence decisions.
    """

    provenance_uncertainty: float = Field(default=0.5, ge=0, le=1)
    integrity_uncertainty: float = Field(default=0.5, ge=0, le=1)
    influence_strength: float = Field(default=0.5, ge=0, le=1)
    persistence: float = Field(default=0.0, ge=0, le=1)
    privilege_amplification: float = Field(default=0.0, ge=0, le=1)
    external_reach: float = Field(default=0.0, ge=0, le=1)
    data_sensitivity: float = Field(default=0.0, ge=0, le=1)
    propagation: float = Field(default=0.0, ge=0, le=1)
    irreversibility: float = Field(default=0.0, ge=0, le=1)

    def score(self) -> int:
        weights = {
            "provenance_uncertainty": 0.14,
            "integrity_uncertainty": 0.12,
            "influence_strength": 0.16,
            "persistence": 0.10,
            "privilege_amplification": 0.14,
            "external_reach": 0.10,
            "data_sensitivity": 0.10,
            "propagation": 0.08,
            "irreversibility": 0.06,
        }
        values = self.model_dump()
        return round(100 * sum(values[k] * w for k, w in weights.items()))


class ContextAsset(BaseModel):
    id: str
    tenant: str
    kind: ContextKind = "other"
    name: str
    source: str
    digest: str
    previous_digest: str | None = None
    version: int = 1
    change_count: int = 0
    trust: TrustLevel = "unknown"
    influence: InfluenceLevel = "medium"
    capabilities: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    tasks: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    risk: ContextRiskVector = Field(default_factory=ContextRiskVector)
    first_seen: datetime
    last_seen: datetime
    changed_at: datetime

    @property
    def exposure_score(self) -> int:
        return self.risk.score()

    def public(self) -> dict[str, Any]:
        return {**self.model_dump(mode="json"), "exposure_score": self.exposure_score}


class ContextSnapshot(BaseModel):
    tenant: str
    asset_id: str
    digest: str
    observed_at: datetime
    document: dict[str, Any]


class ContextLineage(BaseModel):
    tenant: str
    decision_id: str
    asset_ids: list[str] = Field(default_factory=list)
    task: str | None = None
    agent: str | None = None
    created_at: datetime


def stable_asset_id(kind: str, source: str) -> str:
    raw = f"{kind}:{source}".encode()
    return f"ctx_{hashlib.sha256(raw).hexdigest()[:20]}"


def fingerprint_asset(raw: dict[str, Any]) -> str:
    supplied = raw.get("digest")
    if isinstance(supplied, str) and supplied:
        return supplied[:128]
    material = {
        "kind": raw.get("kind") or "other",
        "name": raw.get("name") or raw.get("source") or "context",
        "source": raw.get("source") or raw.get("name") or "context",
        "capabilities": sorted(str(v) for v in (raw.get("capabilities") or [])),
        "provenance": raw.get("provenance") or {},
        "metadata": raw.get("metadata") or {},
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def default_risk(kind: str, trust: str, influence: str, metadata: dict[str, Any]) -> ContextRiskVector:
    trust_uncertainty = {
        "verified": 0.05, "project-bound": 0.10, "repository": 0.18,
        "internal": 0.20, "external": 0.75, "unknown": 0.85,
    }.get(trust, 0.7)
    influence_strength = {"low": 0.25, "medium": 0.55, "high": 0.9}.get(influence, 0.55)
    persistent = kind in {"instruction", "skill", "knowledge", "memory"}
    executable = kind in {"skill", "tool", "mcp", "harness"}
    external = kind in {"mcp", "external"} or trust == "external"
    sensitivity = str(metadata.get("classification") or "").lower()
    sensitivity_score = {"restricted": 0.95, "confidential": 0.8, "internal": 0.45, "public": 0.1}.get(sensitivity, 0.0)
    return ContextRiskVector(
        provenance_uncertainty=trust_uncertainty,
        integrity_uncertainty=min(1.0, trust_uncertainty + (0.15 if external else 0.0)),
        influence_strength=influence_strength,
        persistence=0.9 if kind == "memory" else (0.65 if persistent else 0.15),
        privilege_amplification=0.8 if executable else 0.15,
        external_reach=0.9 if external else (0.55 if kind == "tool" else 0.1),
        data_sensitivity=sensitivity_score,
        propagation=0.75 if kind in {"memory", "skill"} else 0.25,
        irreversibility=0.4 if executable else 0.05,
    )
