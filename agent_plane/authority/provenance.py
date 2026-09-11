"""Optional provenance context on ``POST /v1/authorize``.

agent-plane does not infer where a proposed action came from. An integrator
that knows can say so: the ``context`` block is a small map of string
identifiers that is validated, stored verbatim on the signed audit record
(``obligations_applied`` carries an ``agent-plane.provenance.v1`` entry), and
echoed in the decision response. The console uses ``parent_evidence_id`` to
draw the chain from a model decision, through a tool decision, to execution
receipts.

Recognised keys are listed in :data:`KNOWN_KEYS`; other keys are accepted so
long as they meet the same size limits. Nothing here influences the decision.
"""
from __future__ import annotations

from typing import Any

SCHEMA = "agent-plane.provenance.v1"
KNOWN_KEYS = (
    "parent_evidence_id",   # evidence id of the decision that led here (model/tool call)
    "prompt_hash",          # sha256 of the prompt or instruction that proposed the action
    "conversation_id",      # the session / thread the agent is acting in
    "request_id",           # the integrator's request or trace id
    "run_id",               # orchestrator run / job id
    "origin",               # free-form: "human", "scheduler", "agent:<id>", ...
)
MAX_KEYS = 12
MAX_KEY_LENGTH = 64
MAX_VALUE_LENGTH = 512


def validate_context(raw: Any) -> dict[str, str]:
    """Return a clean ``{str: str}`` map or raise ``ValueError``."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("'context' must be an object of string values")
    if len(raw) > MAX_KEYS:
        raise ValueError(f"'context' may carry at most {MAX_KEYS} keys")
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key or len(key) > MAX_KEY_LENGTH:
            raise ValueError("'context' keys must be non-empty strings")
        if not isinstance(value, str) or len(value) > MAX_VALUE_LENGTH:
            raise ValueError(f"'context.{key}' must be a string of at most {MAX_VALUE_LENGTH} chars")
        cleaned[key] = value
    return cleaned


def provenance_record(context: dict[str, str]) -> dict[str, str]:
    return {"schema": SCHEMA, **context}
