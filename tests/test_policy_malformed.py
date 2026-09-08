"""A malformed policy file must fail closed: startup refuses to come up
ungoverned, and a bad hot-reload leaves the previously-loaded valid engine
untouched rather than corrupting it."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _bad_policy_dir(tmp_path):
    pol_dir = tmp_path / "policies"
    pol_dir.mkdir()
    (pol_dir / "broken.yaml").write_text("name: [this is not: valid: yaml", encoding="utf-8")
    return pol_dir


def test_startup_crashes_on_malformed_policy_file(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", str(_bad_policy_dir(tmp_path)))
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with pytest.raises(Exception):  # malformed YAML raises at startup, not silently ignored
        with TestClient(create_app()):
            pass
    get_settings.cache_clear()


def test_bad_hot_reload_leaves_prior_engine_in_place(tmp_path, monkeypatch):
    pol_dir = tmp_path / "policies"
    pol_dir.mkdir()
    (pol_dir / "ok.yaml").write_text(
        "policy:\n  name: allow-all\n  version: '1'\n"
        "  match: {}\n  decision: { action: allow }\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", str(pol_dir))
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        before = c.get("/admin/policies", headers={"X-Admin-Token": "test-admin"}).json()

        # Corrupt the file in place, then trigger a hot-reload.
        (pol_dir / "ok.yaml").write_text("name: [broken", encoding="utf-8")
        r = c.post("/admin/policies/reload", headers={"X-Admin-Token": "test-admin"})
        assert r.status_code == 500

        after = c.get("/admin/policies", headers={"X-Admin-Token": "test-admin"}).json()
        assert after["policy_version"] == before["policy_version"]
    get_settings.cache_clear()
