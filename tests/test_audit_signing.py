"""Tamper-evidence: hash-chained, HMAC-signed audit events."""
from __future__ import annotations

from agent_plane.audit.signing import sign_event, verify_chain

KEY = "test-audit-key"


def _sign_chain(events: list[dict], key: str = KEY) -> list[dict]:
    out: list[dict] = []
    prev = ""
    for e in events:
        event_hash, signature = sign_event(e, prev, key)
        rec = dict(e)
        rec.update(prev_hash=prev or None, event_hash=event_hash, signature=signature)
        out.append(rec)
        prev = event_hash
    return out


def _events() -> list[dict]:
    return [
        {"decision_id": "dec_1", "decision": "allow", "rules_matched": ["pii"]},
        {"decision_id": "dec_2", "decision": "deny", "rules_matched": ["finance"]},
        {"decision_id": "dec_3", "decision": "approval_required", "rules_matched": []},
    ]


def test_valid_chain_verifies():
    assert verify_chain(_sign_chain(_events()), KEY) is True


def test_tampered_field_breaks_verification():
    signed = _sign_chain(_events())
    signed[0]["decision"] = "deny"  # alter a recorded decision after the fact
    assert verify_chain(signed, KEY) is False


def test_deleted_link_breaks_chain():
    signed = _sign_chain(_events())
    del signed[1]  # remove the middle event; chain no longer links
    assert verify_chain(signed, KEY) is False


def test_wrong_key_fails():
    signed = _sign_chain(_events())
    assert verify_chain(signed, "attacker-key") is False
