from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient

JWT_SECRET = "authority-broker-secret"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    manifest = tmp_path / "authority.yaml"
    manifest.write_text(
        '''version: "3"\nrules:\n  - agent_id: "support-*"\n    task: customer_support\n    tool: search_kb\n    environments: [production]\n    resources: ["kb/*"]\n''',
        encoding="utf-8",
    )
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("AUTHORITY_FILE", str(manifest))

    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _headers() -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": "u1",
            "tenant": "default",
            "department": "support",
            "agent_id": "support-1",
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def test_broker_allows_action_inside_task_authority(client):
    r = client.post(
        "/v1/tools/invoke",
        headers=_headers(),
        json={
            "tool": "search_kb",
            "arguments": {"q": "reset password"},
            "authority": {
                "task": "customer_support",
                "environment": "production",
                "resource": "kb/passwords",
            },
        },
    )
    assert r.status_code == 200
    cp = r.json()["x_control_plane"]
    assert cp["authority_manifest_version"] == "3"
    assert "ai-tm-task-authority" in cp["rules_matched"]


def test_broker_denies_same_tool_outside_task_authority(client):
    r = client.post(
        "/v1/tools/invoke",
        headers=_headers(),
        json={
            "tool": "search_kb",
            "arguments": {"q": "customer"},
            "authority": {
                "task": "export_customer_database",
                "environment": "production",
                "resource": "kb/customers",
            },
        },
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["authority_manifest_version"] == "3"
    assert "task-authority" in detail["reason"]
