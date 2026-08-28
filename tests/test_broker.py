"""The agent->tool broker edge (/v1/tools/invoke)."""
from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient

JWT_SECRET = "broker-secret"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _token(**claims) -> str:
    return jwt.encode({"sub": "u1", "tenant": "default", **claims}, JWT_SECRET, algorithm="HS256")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_missing_auth_returns_401(client):
    r = client.post("/v1/tools/invoke", json={"tool": "search_kb", "arguments": {}})
    assert r.status_code == 401


def test_unknown_tool_default_denied(client):
    r = client.post(
        "/v1/tools/invoke",
        headers=_auth(_token()),
        json={"tool": "definitely_not_a_tool", "arguments": {}},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "unknown_tool"


def test_allowed_tool_executes_and_audits(client):
    r = client.post(
        "/v1/tools/invoke",
        headers=_auth(_token(department="support")),
        json={"tool": "search_kb", "arguments": {"q": "reset password"}},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["result"]["echo"] == {"q": "reset password"}
    cp = data["x_control_plane"]
    assert cp["tool"] == "search_kb" and cp["decision_id"].startswith("dec_")

    # The tool call landed in the same signed audit chain as model calls.
    audit = client.get("/v1/audit", headers={"X-Admin-Token": "test-admin"}).json()["events"]
    assert audit and audit[0]["model_requested"] == "tool:search_kb"
    assert audit[0]["signature"]


def test_sensitive_tool_requires_approval(client):
    r = client.post(
        "/v1/tools/invoke",
        headers=_auth(_token()),
        json={"tool": "wire_transfer", "arguments": {"amount": 1000}},
    )
    assert r.status_code == 202
    assert r.json()["detail"]["error"] == "approval_required"


def test_tool_outside_allowlist_denied(client):
    # Delegation scope is asserted via claims here (dev mode); the broker enforces it.
    token = _token(agent_id="a1", allowed_tools=["search_kb"])
    r = client.post(
        "/v1/tools/invoke",
        headers=_auth(token),
        json={"tool": "echo", "arguments": {"x": 1}},
    )
    assert r.status_code == 403
    assert "echo" in r.json()["detail"]["denied_tools"]


def test_redaction_obligation_applies_to_tool_args_and_result(tmp_path, monkeypatch):
    # A redact obligation on tool_call must scrub both what's sent to the tool
    # and what comes back, same guarantee as the model edge.
    pol_dir = tmp_path / "pol"
    pol_dir.mkdir()
    (pol_dir / "redact-tools.yaml").write_text(
        "policy:\n"
        "  name: redact-tool-pii\n"
        "  version: t\n"
        "  match: { request_type: tool_call }\n"
        "  decision: { action: allow, redact: [email], obligations: [redact_pii] }\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "a.db"))
    monkeypatch.setenv("POLICY_DIR", str(pol_dir))
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        r = c.post(
            "/v1/tools/invoke",
            headers=_auth(_token()),
            json={"tool": "echo", "arguments": {"note": "contact alice@example.com"}},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["result"]["echo"]["note"] == "contact [REDACTED_EMAIL]"
        assert data["x_control_plane"]["redactions_applied"] == ["email"]

        audit = c.get("/v1/audit", headers={"X-Admin-Token": "test-admin"}).json()["events"]
        tool_events = [e for e in audit if e["model_requested"] == "tool:echo"]
        assert tool_events and tool_events[0]["redactions_applied"] == ["email"]
    get_settings.cache_clear()
