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


def test_blank_hosted_environment_uses_defaults(tmp_path, monkeypatch):
    # Hosting dashboards may create empty values for every .env.example key.
    for name in Settings.model_fields:
        monkeypatch.setenv(name.upper(), "")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("AUDIT_SIGNING_KEY", "y" * 40)
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    from agent_plane.config import get_settings
    from agent_plane.main import create_app

    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
            assert c.get("/healthz").status_code == 200
            assert c.get("/readyz").json() == {"status": "ready"}
            assert "agent-plane" in c.get("/console").text
            assert c.get("/v1/audit").status_code == 404  # admin remains disabled
    finally:
        get_settings.cache_clear()


def test_blank_secrets_do_not_bypass_production_checks(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv("AUDIT_SIGNING_KEY", "")
    errors = Settings(environment="production", _env_file=None).production_errors()
    assert any("JWT_SECRET" in error for error in errors)
    assert any("AUDIT_SIGNING_KEY" in error for error in errors)


@pytest.mark.parametrize("workers", ["0", "-1"])
def test_cli_rejects_nonpositive_worker_counts(workers, capsys):
    from agent_plane.cli import main

    with pytest.raises(SystemExit) as error:
        main(["serve", "--workers", workers])
    assert error.value.code == 2
    assert "at least 1" in capsys.readouterr().err


def test_cli_rejects_multiple_workers_with_memory_store(monkeypatch, capsys):
    from agent_plane.cli import main
    from agent_plane.config import get_settings

    monkeypatch.setenv("AUTHORITY_STORE", "memory")
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as error:
            main(["serve", "--workers", "2"])
    finally:
        get_settings.cache_clear()
    assert error.value.code == 2
    assert "process-local" in capsys.readouterr().err


def test_memory_store_is_refused_in_production():
    s = Settings(environment="production", jwt_secret="x" * 40, audit_signing_key="y" * 40,
                 authority_store="memory")
    assert any("AUTHORITY_STORE=memory" in e for e in s.production_errors())
    s = Settings(environment="production", jwt_secret="x" * 40, audit_signing_key="y" * 40,
                 mcp_gateway_file="config/mcp.yaml")
    assert s.production_errors() == []  # the gateway is no longer refused in production


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
    # The built Vite bundle is served from /console/assets/* with immutable caching.
    import re

    assets = re.findall(r'/console/assets/([^"\']+)', page.text)
    assert assets, "the console shell must reference its built assets"
    for asset in assets:
        response = client.get(f"/console/assets/{asset}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(("text/css", "text/javascript"))
        assert "immutable" in response.headers.get("cache-control", "")
    assert client.get("/console/assets/../__init__.py").status_code == 404
    # Client-side routes render the shell; unknown files do not.
    assert client.get("/console/decisions").status_code == 200
    assert client.get("/console/assets/missing.js").status_code == 404
    assert client.get("/brand/mark.svg").headers["content-type"].startswith("image/svg+xml")


def test_production_startup_fails_closed_on_empty_policy_bundle(tmp_path, monkeypatch):
    # A policy dir that exists but whose files all parse to zero policies (as
    # opposed to a missing dir, which falls back to packaged defaults) must
    # not be allowed to boot in production as an unnoticed allow-all.
    pol_dir = tmp_path / "policies"
    pol_dir.mkdir()
    (pol_dir / "empty.yaml").write_text("", encoding="utf-8")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "a.db"))
    monkeypatch.setenv("POLICY_DIR", str(pol_dir))
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("AUDIT_SIGNING_KEY", "y" * 40)
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with pytest.raises(Exception):  # noqa: B017 - lifespan raises RuntimeError
        with TestClient(create_app()):
            pass
    get_settings.cache_clear()


def test_development_still_allows_empty_policy_bundle_with_a_warning(tmp_path, monkeypatch):
    # Same empty bundle, but outside production - must still boot (existing
    # dev-mode allow-all behaviour), just logged loudly.
    pol_dir = tmp_path / "policies"
    pol_dir.mkdir()
    (pol_dir / "empty.yaml").write_text("", encoding="utf-8")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "a.db"))
    monkeypatch.setenv("POLICY_DIR", str(pol_dir))
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        assert c.get("/readyz").status_code == 200
    get_settings.cache_clear()


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
