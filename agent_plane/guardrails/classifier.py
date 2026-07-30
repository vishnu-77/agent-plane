"""Derive a data classification *floor* from request content.

The caller may declare a ``data_classification``, but the caller is exactly the
party the control plane governs - a compromised or prompt-injected agent will
happily label its exfiltration ``public``. So we never trust that label to
*lower* sensitivity: we derive an escalation floor from the content and take the
more sensitive of (declared, derived). The floor only ever escalates.

This is deliberately coarse (keyword + secret signals), not information-flow
control - see the series' Part 5 for why true IFC remains an open problem.
"""
from __future__ import annotations

import re

from agent_plane.guardrails import scanner
from agent_plane.schemas.canonical import DataClassification

# Topics that, if present, mean the payload cannot legitimately be "public".
_SENSITIVE_TOPIC = re.compile(
    r"\b(q[1-4]|revenue|earnings|ebitda|payroll|salary|ssn|nda|litigation|"
    r"lawsuit|merger|acquisition|balance sheet|financial statement|"
    r"confidential|regulated)\b",
    re.IGNORECASE,
)


def derive_classification(messages: list[dict]) -> DataClassification:
    """Return the minimum classification the content warrants.

    Returns ``PUBLIC`` when no signal is found (imposing no escalation), so a
    caller can still legitimately mark benign data public; returns
    ``CONFIDENTIAL`` when a secret or a sensitive topic is detected.
    """
    text = "\n".join(str(m.get("content") or "") for m in messages)
    if not text.strip():
        return DataClassification.PUBLIC
    # A live secret in the payload is confidential regardless of any label.
    if scanner.scan(text, ["api_key"]):
        return DataClassification.CONFIDENTIAL
    if _SENSITIVE_TOPIC.search(text):
        return DataClassification.CONFIDENTIAL
    return DataClassification.PUBLIC
