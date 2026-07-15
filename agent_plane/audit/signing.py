"""Tamper-evidence for audit events.

Each event is hash-chained to its predecessor and HMAC-signed, so a row can no
longer be silently altered after the fact: changing any field (or reordering the
chain) breaks verification. This is the cheap, dependency-free first step the
series' Part 6 describes ("sign the decision fingerprint, hash-chain the log");
an Ed25519 signature is a drop-in upgrade for third-party verifiability.
"""
from __future__ import annotations

import hashlib
import hmac
import json

# Fields that are not part of the signed payload (they are the signature itself,
# or assigned by the database after signing).
_NON_PAYLOAD = {"prev_hash", "event_hash", "signature", "created_at", "id"}


def _digest(event: dict) -> str:
    payload = {k: v for k, v in event.items() if k not in _NON_PAYLOAD}
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def sign_event(event: dict, prev_hash: str, key: str) -> tuple[str, str]:
    """Return ``(event_hash, signature)`` for an event chained to ``prev_hash``."""
    chained = (_digest(event) + (prev_hash or "")).encode()
    event_hash = hashlib.sha256(chained).hexdigest()
    signature = hmac.new(key.encode(), event_hash.encode(), hashlib.sha256).hexdigest()
    return event_hash, signature


def verify_chain(events: list[dict], key: str) -> bool:
    """Verify a chronologically-ordered list of signed events.

    Returns ``True`` only if every link recomputes to its stored hash/signature
    and the chain is unbroken.
    """
    prev = ""
    for event in events:
        chained = (_digest(event) + prev).encode()
        expected_hash = hashlib.sha256(chained).hexdigest()
        if event.get("event_hash") != expected_hash:
            return False
        if event.get("prev_hash") != (prev or None) and event.get("prev_hash") != prev:
            return False
        expected_sig = hmac.new(
            key.encode(), expected_hash.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(event.get("signature") or "", expected_sig):
            return False
        prev = expected_hash
    return True
