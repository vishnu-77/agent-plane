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


def test_walkthrough_is_served_read_only(gateway):
    client, _ = gateway
    assert client.get('/flow').status_code == 200
    assert 'Integration walkthrough' in client.get('/flow').text
    assert client.post('/flow').status_code == 405
