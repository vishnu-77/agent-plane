"""PII / secret scanning and redaction.

Runs only when a Decision carries a redaction obligation. Each detector maps to
a field name used in policy ``redact`` lists (``email``, ``credit_card``,
``phone``, ``api_key``). Redaction is applied to request prompts and to the
upstream response.
"""
from __future__ import annotations

import re

_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    # 13-16 digit sequences, optionally separated by spaces/dashes.
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "phone": re.compile(r"\b(?:\+?\d{1,3}[ -]?)?(?:\(?\d{3}\)?[ -]?)\d{3}[ -]?\d{4}\b"),
    # Common API-key shapes (OpenAI sk-..., generic long tokens, AWS-ish).
    "api_key": re.compile(r"\b(?:sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|[A-Za-z0-9_\-]{32,})\b"),
}

_MASKS = {
    "email": "[REDACTED_EMAIL]",
    "credit_card": "[REDACTED_CC]",
    "phone": "[REDACTED_PHONE]",
    "api_key": "[REDACTED_KEY]",
}


def scan(text: str, fields: list[str]) -> list[str]:
    """Return the subset of ``fields`` detected in ``text``."""
    found = []
    for field in fields:
        pattern = _PATTERNS.get(field)
        if pattern and pattern.search(text):
            found.append(field)
    return found


def redact(text: str, fields: list[str]) -> tuple[str, list[str]]:
    """Redact the requested ``fields`` from ``text``.

    Returns the redacted text and the list of fields that actually matched.
    Order matters: credit_card/api_key before phone to avoid partial overlaps.
    """
    if not text:
        return text, []
    ordered = [f for f in ("email", "credit_card", "api_key", "phone") if f in fields]
    hit: list[str] = []
    for field in ordered:
        pattern = _PATTERNS[field]
        if pattern.search(text):
            hit.append(field)
            text = pattern.sub(_MASKS[field], text)
    return text, hit


def redact_messages(
    messages: list[dict], fields: list[str]
) -> tuple[list[dict], list[str]]:
    """Redact ``content`` across a list of chat messages."""
    all_hits: list[str] = []
    out: list[dict] = []
    for msg in messages:
        msg = dict(msg)
        content = msg.get("content")
        if isinstance(content, str):
            redacted, hits = redact(content, fields)
            msg["content"] = redacted
            for h in hits:
                if h not in all_hits:
                    all_hits.append(h)
        out.append(msg)
    return out, all_hits
