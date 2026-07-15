"""Production-readiness: fail-closed config, packaged defaults, ops endpoints."""
from __future__ import annotations

import argparse

import pytest
from fastapi.testclient import TestClient

from agent_plane.config import Settings
from agent_plane.policy.loader import load_bundle


def test_production_rejects_default_secrets():
    errs = Settings(environment="production").production_errors()
    assert any("JWT_SECRET" in e for e in errs)
    assert any("AUDIT_SIGNING_KEY" in e for e in errs)


def test_production_ok_with_strong_secrets():
    s = Settings(environment="production", jwt_secret="x" * 40, audit_signing_key="y" * 40)
    assert s.production_errors() == []


def test_production_delegation_requires_key():
    s = Settings(
        environment="production",
        jwt_secret="x" * 40,
        audit_signing_key="y" * 40,
        identity_mode="delegation",
    )
    assert any("DELEGATION_PUBLIC_KEY" in e for e in s.production_errors())


def test_development_has_no_production_errors():
    assert Settings().production_errors() == []


def test_load_bundle_falls_back_to_packaged_defaults(tmp_path):
    # A fresh install with no policies in CWD must still be governed (not allow-all).
    bundle = load_bundle(str(tmp_path / "does-not-exist"))
    assert bundle.policies
    assert "pii-redaction-required" in [p.name for p in bundle.policies]


def test_init_scaffolds_defaults(tmp_path):
    from agent_plane.cli import _init

    _init(argparse.Namespace(dir=str(tmp_path), force=False))
    assert (tmp_path / "policies" / "pii-redaction-required.yaml").exists()
    assert (tmp_path / "config" / "models.yaml").exists()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def test_readyz_ok(client):
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["status"] == "ready"


def test_request_id_header_present(client):
    r = client.get("/healthz")
    assert r.headers.get("X-Request-ID")


def test_console_and_root_redirect(client):
    page = client.get("/console")
    assert page.status_code == 200 and "agent-plane" in page.text
    root = client.get("/", follow_redirects=False)
    assert root.status_code in (307, 308)
    assert root.headers["location"].endswith("/console")


def test_production_startup_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "a.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    # Leave the default (insecure) secrets in place -> must refuse to start.
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("AUDIT_SIGNING_KEY", raising=False)
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with pytest.raises(Exception):  # noqa: B017 - lifespan raises RuntimeError
        with TestClient(create_app()):
            pass
    get_settings.cache_clear()
