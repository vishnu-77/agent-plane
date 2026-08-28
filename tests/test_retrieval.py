"""RAG authorization edge: identity-aware retrieval (/v1/retrieve)."""
from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient

JWT_SECRET = "rag-secret"


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


def _auth(**claims):
    token = jwt.encode({"sub": "u1", "tenant": "default", **claims}, JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def _retrieve(client, headers, query, source="kb", top_k=5):
    return client.post(
        "/v1/retrieve", headers=headers, json={"source": source, "query": query, "top_k": top_k}
    )


def test_missing_auth_401(client):
    assert _retrieve(client, {}, "password reset").status_code == 401


def test_unknown_source_404(client):
    r = _retrieve(client, _auth(department="support"), "x", source="nope")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "unknown_source"


def test_authorized_document_returned(client):
    r = _retrieve(client, _auth(department="support", clearance="internal"), "password reset")
    assert r.status_code == 200
    ids = [d["id"] for d in r.json()["results"]]
    assert "support-reset" in ids


def test_relevance_is_not_permission(client):
    # "severance policy" matches hr-severance best, but a support user may not see it.
    r = _retrieve(client, _auth(department="support", clearance="confidential"), "severance policy")
    assert r.status_code == 200
    body = r.json()
    assert body["results"] == []  # the most relevant doc was filtered out
    assert body["x_control_plane"]["filtered_by_authorization"] >= 1


def test_tenant_isolation(client):
    # tenant-acme-note belongs to tenant "acme"; a default-tenant user never gets it.
    r = _retrieve(client, _auth(tenant="default", department="support"), "Acme renewal")
    assert r.status_code == 200
    assert r.json()["results"] == []
    assert r.json()["x_control_plane"]["filtered_by_authorization"] >= 1


def test_clearance_filters_below_classification(client):
    # finance-forecast is confidential; a finance user with only internal clearance can't see it.
    low = _retrieve(client, _auth(department="finance", clearance="internal"), "revenue forecast")
    assert low.json()["results"] == []
    high = _retrieve(client, _auth(department="finance", clearance="confidential"), "revenue forecast")
    assert [d["id"] for d in high.json()["results"]] == ["finance-forecast"]


def test_group_acl_gates_access(client):
    # hr-severance also requires the hr-admins group.
    no_group = _auth(department="hr", clearance="confidential")
    assert _retrieve(client, no_group, "severance").json()["results"] == []
    admin = _auth(department="hr", clearance="confidential", groups=["hr-admins"])
    assert [d["id"] for d in _retrieve(client, admin, "severance").json()["results"]] == ["hr-severance"]


def test_retrieve_is_audited_and_metered(client):
    _retrieve(client, _auth(department="support"), "password reset")
    audit = client.get("/v1/audit", headers={"X-Admin-Token": "test-admin"}).json()["events"]
    assert any(e["model_requested"] == "rag:kb" for e in audit)
    usage = client.get("/v1/usage", headers=_auth(department="support")).json()
    assert any(i["resource"] == "kb" and i["edge"] == "retrieve" for i in usage["items"])


def test_source_level_policy_can_deny(tmp_path, monkeypatch):
    # A YAML rule matching request_type=retrieval denies the whole source.
    pol_dir = tmp_path / "pol"
    pol_dir.mkdir()
    (pol_dir / "no-retrieval.yaml").write_text(
        "policy:\n"
        "  name: deny-support-retrieval\n"
        "  version: t\n"
        "  scope: { departments: [support] }\n"
        "  match: { request_type: retrieval }\n"
        "  decision: { action: deny, reason: 'no retrieval for support' }\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "a.db"))
    monkeypatch.setenv("POLICY_DIR", str(pol_dir))
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        r = _retrieve(c, _auth(department="support"), "password reset")
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "denied_by_policy"
    get_settings.cache_clear()


def test_redaction_obligation_applies_to_rag_results(tmp_path, monkeypatch):
    # A redact obligation on retrieval must scrub PII out of returned document
    # text before it reaches the caller - relevance/authorization alone don't.
    kb_file = tmp_path / "knowledge.yaml"
    kb_file.write_text(
        "sources:\n"
        "  - name: kb\n"
        "    type: local\n"
        "    documents:\n"
        "      - id: doc1\n"
        '        text: "Contact support at alice@example.com for a reset."\n'
        "        classification: public\n",
        encoding="utf-8",
    )
    pol_dir = tmp_path / "pol"
    pol_dir.mkdir()
    (pol_dir / "redact-rag.yaml").write_text(
        "policy:\n"
        "  name: redact-rag-pii\n"
        "  version: t\n"
        "  match: { request_type: retrieval }\n"
        "  decision: { action: allow, redact: [email], obligations: [redact_pii] }\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "a.db"))
    monkeypatch.setenv("POLICY_DIR", str(pol_dir))
    monkeypatch.setenv("KNOWLEDGE_FILE", str(kb_file))
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        r = _retrieve(c, _auth(), "reset")
        assert r.status_code == 200
        body = r.json()
        assert "[REDACTED_EMAIL]" in body["results"][0]["text"]
        assert body["x_control_plane"]["redactions_applied"] == ["email"]
    get_settings.cache_clear()
