"""Load and version policy bundles from ``policies/*.yaml``.

The loader parses each file into a :class:`Policy`, tolerating the doc's natural
YAML shape (unknown ``decision`` keys are preserved under ``extra``). The active
bundle version is a deterministic fingerprint of the loaded policies, stamped on
every audit event so a decision can be tied to the exact ruleset in effect.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from agent_plane.policy.schema import (
    Policy,
    PolicyDecisionSpec,
    PolicyException,
    PolicyMatch,
    PolicyScope,
)

_KNOWN_DECISION_KEYS = {
    "action",
    "reason",
    "route",
    "max_tokens",
    "redact",
    "log_level",
    "cache_ttl",
    "obligations",
}


def _parse_decision(raw: dict) -> PolicyDecisionSpec:
    raw = dict(raw or {})
    extra = {k: v for k, v in raw.items() if k not in _KNOWN_DECISION_KEYS}
    known = {k: v for k, v in raw.items() if k in _KNOWN_DECISION_KEYS}
    return PolicyDecisionSpec(extra=extra, **known)


def _parse_policy(doc: dict) -> Policy:
    return Policy(
        name=doc["name"],
        version=str(doc.get("version", "0")),
        scope=PolicyScope(**(doc.get("scope") or {})),
        match=PolicyMatch(**(doc.get("match") or {})),
        decision=_parse_decision(doc.get("decision") or {}),
        obligations=doc.get("obligations") or [],
        exceptions=[PolicyException(**e) for e in (doc.get("exceptions") or [])],
    )


class PolicyBundle:
    """An ordered, versioned set of loaded policies."""

    def __init__(self, policies: list[Policy]):
        self.policies = policies
        self.version = self._fingerprint(policies)

    @staticmethod
    def _fingerprint(policies: list[Policy]) -> str:
        payload = json.dumps(
            [p.model_dump() for p in policies], sort_keys=True, default=str
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()[:8]
        return f"bundle-{len(policies)}-{digest}"


def load_bundle(policy_dir: str) -> PolicyBundle:
    directory = Path(policy_dir)
    paths = sorted(directory.glob("*.yaml")) if directory.exists() else []
    if not paths:
        # No policies in the working dir -> fall back to the bundled defaults,
        # so a fresh install is governed (not silently allow-all).
        from agent_plane.defaults import default_policies_dir

        paths = sorted(default_policies_dir().glob("*.yaml"))
    policies: list[Policy] = []
    for path in paths:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not doc:
            continue
        # Files may wrap content under a top-level ``policy:`` key, or be flat.
        doc = doc.get("policy", doc)
        policies.append(_parse_policy(doc))
    return PolicyBundle(policies)
