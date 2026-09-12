"""MCP HTTP boundary, independent of upstream availability."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest
import yaml

pytest.importorskip("mcp")
from fastapi.testclient import TestClient


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    from examples.mcp_gateway_demo import configuration, lease_data

    config = configuration(9999)
    config["upstream_secret_env"] = None
    path = tmp_path / "gateway.yaml"
    path.write_text(yaml.safe_dump(config))
    leases = tmp_path / "leases.yaml"
    leases.write_text(yaml.safe_dump({"leases": [lease_data()]}))
    for key, value in {"MCP_GATEWAY_FILE": str(path), "LEASES_FILE": str(leases), "JWT_SECRET": "a"*40,
        "ADMIN_TOKEN": "test-admin", "ENVIRONMENT": "development", "IDENTITY_MODE": "jwt_claims",
        "SQLITE_PATH": str(tmp_path / "audit.db"), "STORAGE_BACKEND": "local", "MAX_REQUEST_BYTES": "4096"}.items():
        monkeypatch.setenv(key, value)
    from agent_plane.config import get_settings
    get_settings.cache_clear()
    from agent_plane.main import create_app
    token = jwt.encode({"sub": "operator", "tenant": "acme", "agent_id": "repo-agent",
                        "allowed_tools": ["branch", "repo.branches", "repo.delete_branch"],
                        "exp": datetime.now(UTC) + timedelta(minutes=5)}, "a"*40, algorithm="HS256")
    with TestClient(create_app(), base_url="http://localhost:8000") as client:
        yield client, {"Authorization": "Bearer " + token, "MCP-Protocol-Version": "2026-07-28",
                       "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    get_settings.cache_clear()


def test_missing_identity_and_unsupported_protocol(gateway):
    client, headers = gateway
    assert client.post('/mcp', json={}).status_code == 401
    assert client.post('/mcp', json={}, headers={**headers, 'MCP-Protocol-Version': '2025-11-25'}).status_code == 400
    assert client.get('/mcp', headers=headers).status_code == 405


def test_a_project_api_key_is_accepted(gateway):
    """`agentplane connect mcp` hands the MCP client a Project API Key.

    The gateway has to accept it, or the one command the console prints does
    not work. Authority is still not inferred from the key: the (tenant, agent)
    pair must match a binding in the operator's mapping file.
    """
    client, headers = gateway
    accounts = client.app.state.accounts
    user = accounts.create_user(email="mcp@example.com", password="correct-horse-battery")
    workspace = accounts.create_workspace(name="acme", owner=user.id)
    # The demo mapping binds tenant "acme" to the repo-agent's task and lease.
    accounts.create_project(workspace_id=workspace.id, name="acme", created_by=user.id,
                            project_id="acme")
    _, secret = accounts.create_key(project_id="acme", name="mcp", created_by=user.id)

    key_headers = {**headers, "Authorization": f"Bearer {secret}", "X-Agent-Id": "repo-agent"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "repo.delete_branch", "arguments": {"branch": "main"}, "_meta": {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {}}}}
    response = client.post("/mcp", headers={**key_headers, "Mcp-Method": "tools/call",
                                            "Mcp-Name": "repo.delete_branch"}, json=body)
    assert response.status_code == 200, response.text
    assert "RESOURCE_PROTECTED" in response.text     # decided, not refused at the door
    assert "not_dispatched" in response.text

    # A revoked or unknown key is still nobody.
    assert client.post("/mcp", headers={**headers, "Authorization": "Bearer ap_live_nope"},
                       json={}).status_code == 401


def test_origin_and_body_bounds(gateway):
    client, headers = gateway
    assert client.post('/mcp', headers={**headers, 'Origin': 'https://untrusted.example'}, json={}).status_code == 403
    assert client.post('/mcp', headers=headers, content='x'*5000).status_code == 413


def test_modern_protocol_metadata_must_match(gateway):
    client, headers = gateway
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {'_meta': {
        'io.modelcontextprotocol/protocolVersion': '2026-07-28',
        'io.modelcontextprotocol/clientInfo': {'name': 'test', 'version': '1'},
        'io.modelcontextprotocol/clientCapabilities': {}}}}
    response = client.post('/mcp', headers={**headers, 'Mcp-Method': 'tools/call'}, json=body)
    assert response.status_code == 400


def test_protected_call_returns_decision_without_contacting_upstream(gateway):
    client, headers = gateway
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
        'name': 'repo.delete_branch', 'arguments': {'branch': 'main'}, '_meta': {
        'io.modelcontextprotocol/protocolVersion': '2026-07-28',
        'io.modelcontextprotocol/clientInfo': {'name': 'test', 'version': '1'},
        'io.modelcontextprotocol/clientCapabilities': {}}}}
    response = client.post('/mcp', headers={**headers, 'Mcp-Method': 'tools/call', 'Mcp-Name': 'repo.delete_branch'}, json=body)
    assert response.status_code == 200, response.text
    assert 'RESOURCE_PROTECTED' in response.text, response.text
    assert 'not_dispatched' in response.text
    assert 'main' not in json.dumps(response.json().get('error', {}))


def test_console_is_served_read_only(gateway):
    client, _ = gateway
    assert client.get('/console').status_code == 200
    assert 'agent-plane' in client.get('/console').text
    assert client.post('/console').status_code == 405
