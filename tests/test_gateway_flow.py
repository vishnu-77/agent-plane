"""End-to-end gateway flow via FastAPI TestClient.

The upstream provider call is patched so the whole flow runs offline: identity ->
normalize -> policy -> guardrails -> route -> filter -> audit.
"""
from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient

JWT_SECRET = "test-secret"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")

    # Reset cached settings so env overrides take effect.
    from agent_plane.config import get_settings

    get_settings.cache_clear()

    # Patch the real upstream call with a canned OpenAI-shaped response that
    # contains PII, so we can assert response-side redaction.
    from agent_plane.routing.providers import OpenAIProvider

    async def fake_chat(self, body, upstream_model):  # noqa: ANN001
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": upstream_model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Reply with leak agent@togro.co inside.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr(OpenAIProvider, "chat", fake_chat)

    from agent_plane.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c

    get_settings.cache_clear()


def _token(**claims) -> str:
    payload = {"sub": "u1", "tenant": "default", **claims}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_missing_auth_returns_401(client):
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4.1", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401


def test_normal_request_routes_and_redacts_response(client):
    resp = client.post(
        "/v1/chat/completions",
        headers=_auth(_token(department="support")),
        json={
            "model": "gpt-4.1",
            "messages": [{"role": "user", "content": "my card 4111 1111 1111 1111"}],
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    # Response PII redacted by the guardrail engine.
    content = data["choices"][0]["message"]["content"]
    assert "agent@togro.co" not in content
    assert "[REDACTED_EMAIL]" in content

    # Control-plane metadata surfaced.
    cp = data["x_control_plane"]
    assert cp["decision_id"].startswith("dec_")
    assert "pii-redaction-required" in cp["rules_matched"]
    assert cp["model_used"] == "gpt-4.1"

    # Audit row written with the policy fingerprint (admin-only endpoint).
    audit = client.get("/v1/audit", headers={"X-Admin-Token": "test-admin"}).json()["events"]
    assert audit and audit[0]["decision_id"] == cp["decision_id"]
    assert audit[0]["policy_version"] == cp["policy_version"]
    assert "credit_card" in audit[0]["redactions_applied"]


def test_confidential_finance_external_model_denied(client):
    resp = client.post(
        "/v1/chat/completions",
        headers=_auth(_token(department="finance")),
        json={
            "model": "gpt-4.1",
            "data_classification": "confidential",
            "messages": [{"role": "user", "content": "summarize Q3 numbers"}],
        },
    )
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["error"] == "denied_by_policy"
    assert "finance-data-external-model-restriction" in detail["rules_matched"]


def test_confidential_finance_allowed_on_private_azure(client):
    resp = client.post(
        "/v1/chat/completions",
        headers=_auth(_token(department="finance")),
        json={
            "model": "azure-private-gpt4",
            "data_classification": "confidential",
            "messages": [{"role": "user", "content": "summarize Q3 numbers"}],
        },
    )
    # Azure provider isn't patched and has no creds, so it fails upstream (502),
    # but crucially it was NOT denied by policy (the exception allowed it).
    assert resp.status_code == 502


def test_caller_supplied_public_label_is_overridden_by_content(client):
    # Finance user mislabels confidential data as "public"; the derived floor
    # escalates it and the finance rule denies anyway.
    resp = client.post(
        "/v1/chat/completions",
        headers=_auth(_token(department="finance")),
        json={
            "model": "gpt-4.1",
            "data_classification": "public",
            "messages": [{"role": "user", "content": "the Q3 revenue and earnings"}],
        },
    )
    assert resp.status_code == 403
    assert (
        "finance-data-external-model-restriction"
        in resp.json()["detail"]["rules_matched"]
    )


def test_agent_tool_outside_allowlist_denied(client):
    token = _token(agent_id="a1", allowed_tools=["search"])
    resp = client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        json={
            "model": "gpt-4.1",
            "messages": [{"role": "user", "content": "move the money"}],
            "tools": [{"type": "function", "function": {"name": "wire_transfer"}}],
        },
    )
    assert resp.status_code == 403
    assert "wire_transfer" in resp.json()["detail"]["denied_tools"]


def test_audit_chain_is_signed_and_verifiable(client):
    for content in ("first request", "second request"):
        client.post(
            "/v1/chat/completions",
            headers=_auth(_token(department="support")),
            json={"model": "gpt-4.1", "messages": [{"role": "user", "content": content}]},
        )
    events = client.get("/v1/audit", headers={"X-Admin-Token": "test-admin"}).json()["events"]
    assert events and all(e["signature"] for e in events)

    from agent_plane.audit.signing import verify_chain
    from agent_plane.config import get_settings

    # /v1/audit returns newest-first; verify in chronological order.
    chronological = list(reversed(events))
    assert verify_chain(chronological, get_settings().audit_signing_key) is True
