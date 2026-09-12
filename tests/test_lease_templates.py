"""Lease templates: issue a scoped lease from a named shape without letting
the caller widen it."""
from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient

from agent_plane.authority.templates import LeaseTemplate

JWT_SECRET = "template-secret"
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


def _auth(agent_id: str) -> dict[str, str]:
    token = jwt.encode({"sub": "u1", "tenant": "acme", "agent_id": agent_id,
                        "allowed_tools": ["deployment", "branch", "repository"]},
                       JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def test_templates_are_listed_with_their_variables(client):
    r = client.get("/v1/lease-templates", headers=ADMIN)
    assert r.status_code == 200
    by_name = {t["name"]: t for t in r.json()["templates"]}
    assert by_name["repair-service"]["variables"] == ["env", "service"]
    assert by_name["clean-branches"]["variables"] == ["owner", "repo"]


def test_issue_from_template_scopes_resources(client):
    r = client.post("/v1/leases/from-template", headers=ADMIN, json={
        "template": "repair-service", "subject": "devops-2", "task": "fix-search", "tenant": "acme",
        "variables": {"env": "staging", "service": "search"}})
    assert r.status_code == 200, r.text
    lease = r.json()["lease"]
    assert lease["resources"] == ["staging/search"]
    assert lease["protected_resources"] == ["production/*"]
    assert lease["require_approval"] == ["deployment.restart"]
    assert lease["expires_at"] is not None
    assert lease["id"].startswith("lease-repair-service-")
    assert lease["tenant"] == "acme"

    ok = client.post("/v1/authorize", headers=_auth("devops-2"), json={
        "task": "fix-search", "action": "deployment.read", "resource": "staging/search"})
    assert ok.status_code == 200
    out = client.post("/v1/authorize", headers=_auth("devops-2"), json={
        "task": "fix-search", "action": "deployment.read", "resource": "staging/checkout"})
    assert out.status_code == 403
    assert out.json()["detail"]["reason"] == "RESOURCE_OUTSIDE_DELEGATED_SCOPE"


def test_variables_cannot_inject_globs_or_traversal(client):
    for bad in ("*", "staging/*", "..", "a b", "x?", "[a]"):
        r = client.post("/v1/leases/from-template", headers=ADMIN, json={
            "template": "repair-service", "subject": "s", "task": "t",
            "variables": {"env": bad, "service": "svc"}})
        assert r.status_code == 400, bad


def test_missing_unknown_variables_and_templates(client):
    r = client.post("/v1/leases/from-template", headers=ADMIN, json={
        "template": "repair-service", "subject": "s", "task": "t", "variables": {"env": "staging"}})
    assert r.status_code == 400 and "missing" in r.json()["detail"]
    r = client.post("/v1/leases/from-template", headers=ADMIN, json={
        "template": "repair-service", "subject": "s", "task": "t",
        "variables": {"env": "staging", "service": "x", "extra": "y"}})
    assert r.status_code == 400 and "unknown" in r.json()["detail"]
    r = client.post("/v1/leases/from-template", headers=ADMIN, json={
        "template": "nope", "subject": "s", "task": "t"})
    assert r.status_code == 404


def test_explicit_id_conflict_and_admin_gate(client):
    body = {"template": "inspect-deployment", "subject": "s", "task": "t",
            "variables": {"env": "staging"}, "id": "lease-fixed"}
    assert client.post("/v1/leases/from-template", headers=ADMIN, json=body).status_code == 200
    assert client.post("/v1/leases/from-template", headers=ADMIN, json=body).status_code == 409
    assert client.post("/v1/leases/from-template", json=body).status_code == 401


def test_template_placeholder_validation():
    with pytest.raises(ValueError):
        LeaseTemplate(name="bad", resources=["{env!r}/x"])
    with pytest.raises(ValueError):
        LeaseTemplate(name="bad", resources=["{0}/x"])
    t = LeaseTemplate(name="ok", resources=["{env}/{svc}"], actions=["a.b"], ttl_seconds=60)
    lease = t.render(subject="s", task="t", variables={"env": "e", "svc": "v"})
    assert lease.resources == ["e/v"] and lease.child_authority == "none"
