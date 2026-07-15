"""Guardrail scanner: detection + redaction."""
from __future__ import annotations

from agent_plane.guardrails.scanner import redact, redact_messages, scan

FIELDS = ["email", "credit_card", "phone", "api_key"]


def test_detects_email_and_redacts():
    text = "contact me at jane.doe@example.com please"
    assert "email" in scan(text, FIELDS)
    out, hits = redact(text, FIELDS)
    assert "email" in hits
    assert "jane.doe@example.com" not in out
    assert "[REDACTED_EMAIL]" in out


def test_redacts_credit_card():
    out, hits = redact("card 4111 1111 1111 1111 ok", FIELDS)
    assert "credit_card" in hits
    assert "4111" not in out


def test_redacts_api_key():
    out, hits = redact("key sk-abcdef0123456789ABCDEF here", FIELDS)
    assert "api_key" in hits
    assert "sk-abcdef0123456789ABCDEF" not in out


def test_no_false_positive_on_clean_text():
    out, hits = redact("the quick brown fox", FIELDS)
    assert hits == []
    assert out == "the quick brown fox"


def test_redact_messages_across_list():
    msgs = [
        {"role": "user", "content": "email a@b.com"},
        {"role": "assistant", "content": "no pii here"},
    ]
    out, hits = redact_messages(msgs, FIELDS)
    assert "email" in hits
    assert "a@b.com" not in out[0]["content"]
    assert out[1]["content"] == "no pii here"
