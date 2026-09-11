"""Provenance context on /v1/authorize, /metrics, and JSON logging."""
from __future__ import annotations

import json
import logging

import jwt
import pytest
from fastapi.testclient import TestClient

from agent_plane.observability import JsonFormatter, Metrics, _route_label

JWT_SECRET = "prov-secret"
ADMIN = {"X-Admin-Token": "test-admin"}


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


def _auth() -> dict[str, str]:
    token = jwt.encode({"sub": "u1", "tenant": "default", "agent_id": "devops-agent"},
                       JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def _events(client) -> list[dict]:
    body = client.get("/v1/audit?limit=20", headers=ADMIN).json()
    return body["events"] if isinstance(body, dict) else body


def test_context_is_recorded_and_echoed(client):
    context = {"parent_evidence_id": "dec_123", "conversation_id": "conv-9", "origin": "human"}
    r = client.post("/v1/authorize", headers=_auth(), json={
        "task": "fix-staging-checkout", "action": "deployment.restart",
        "resource": "staging/checkout", "context": context})
    assert r.status_code == 200
    assert r.json()["context"] == context
    event = next(e for e in _events(client) if e["decision_id"] == r.json()["evidence_id"])
    prov = next(o for o in event["obligations_applied"]
                if isinstance(o, dict) and o.get("schema") == "agent-plane.provenance.v1")
    assert prov["parent_evidence_id"] == "dec_123"
    assert prov["origin"] == "human"


def test_context_is_validated(client):
    base = {"task": "fix-staging-checkout", "action": "deployment.restart", "resource": "staging/checkout"}
    assert client.post("/v1/authorize", headers=_auth(), json={**base, "context": "nope"}).status_code == 400
    assert client.post("/v1/authorize", headers=_auth(), json={**base, "context": {"k": 1}}).status_code == 400
    assert client.post("/v1/authorize", headers=_auth(),
                       json={**base, "context": {"k": "x" * 513}}).status_code == 400
    assert client.post("/v1/authorize", headers=_auth(),
                       json={**base, "context": {f"k{i}": "v" for i in range(13)}}).status_code == 400
    # Context never changes the decision.
    r = client.post("/v1/authorize", headers=_auth(), json={
        **base, "resource": "production/checkout", "context": {"origin": "human"}})
    assert r.status_code == 403


def test_metrics_endpoint_renders_prometheus_text(client):
    client.post("/v1/authorize", headers=_auth(), json={
        "task": "fix-staging-checkout", "action": "deployment.restart", "resource": "staging/checkout"})
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    text = r.text
    assert 'agent_plane_http_requests_total{method="POST",route="/v1/authorize",status="200"}' in text
    assert 'agent_plane_decisions_total{edge="authorize",decision="allow",reason="ACTION_WITHIN_TASK_AUTHORITY"}' in text
    assert "agent_plane_http_request_seconds_bucket" in text


def test_route_labels_bound_cardinality():
    assert _route_label("/v1/leases/lease-abc") == "/v1/leases/{id}"
    assert _route_label("/v1/leases/lease-abc/delegate") == "/v1/leases/{id}/delegate"
    assert _route_label("/v1/leases/from-template") == "/v1/leases/from-template"
    assert _route_label("/v1/approvals/apr_1/approve") == "/v1/approvals/{id}/approve"
    assert _route_label("/healthz") == "/healthz"


def test_metrics_histogram_counts():
    m = Metrics()
    m.observe_request("GET", "/healthz", 200, 0.002)
    m.observe_request("GET", "/healthz", 200, 3.0)
    out = m.render()
    assert 'agent_plane_http_request_seconds_bucket{route="/healthz",le="0.005"} 1' in out
    assert 'agent_plane_http_request_seconds_bucket{route="/healthz",le="+Inf"} 2' in out
    assert 'agent_plane_http_request_seconds_count{route="/healthz"} 2' in out


def test_json_formatter_includes_extra_fields():
    record = logging.LogRecord("agent_plane", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.request_id = "abc"
    line = JsonFormatter().format(record)
    payload = json.loads(line)
    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "abc"
    assert "ts" in payload
