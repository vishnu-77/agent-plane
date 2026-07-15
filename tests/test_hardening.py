"""Abuse protection: request-size cap + rate limiting."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _app(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "a.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    return create_app()


def _clear():
    from agent_plane.config import get_settings

    get_settings.cache_clear()


def test_request_too_large_returns_413(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, MAX_REQUEST_BYTES="200")
    with TestClient(app) as c:
        body = {"model": "gpt-4.1", "messages": [{"role": "user", "content": "x" * 500}]}
        r = c.post("/v1/chat/completions", json=body)
        assert r.status_code == 413
        assert r.headers.get("X-Request-ID")
    _clear()


def test_rate_limit_returns_429(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, RATE_LIMIT_PER_MINUTE="3")
    with TestClient(app) as c:
        codes = [c.get("/healthz").status_code for _ in range(5)]
        assert codes.count(200) == 3
        assert 429 in codes
    _clear()
