"""Threat-model freshness gate (agent_plane.authority.freshness)."""
from __future__ import annotations

from agent_plane.authority.freshness import validate_threat_model_freshness


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def test_matching_versions_pass(tmp_path):
    cap = _write(tmp_path, "cap.yaml", 'version: "3"\n')
    tm = _write(tmp_path, "tm.yaml", 'capability_manifest_version: "3"\n')
    result = validate_threat_model_freshness(cap, tm)
    assert result.ok is True
    assert result.manifest_version == "3"


def test_drifted_versions_fail(tmp_path):
    cap = _write(tmp_path, "cap.yaml", 'version: "4"\n')
    tm = _write(tmp_path, "tm.yaml", 'capability_manifest_version: "3"\n')
    result = validate_threat_model_freshness(cap, tm)
    assert result.ok is False
    assert "stale" in result.reason


def test_missing_manifest_version_fails(tmp_path):
    cap = _write(tmp_path, "cap.yaml", 'capabilities: []\n')
    tm = _write(tmp_path, "tm.yaml", 'capability_manifest_version: "1"\n')
    result = validate_threat_model_freshness(cap, tm)
    assert result.ok is False
    assert result.manifest_version is None


def test_missing_threat_model_reference_fails(tmp_path):
    cap = _write(tmp_path, "cap.yaml", 'version: "1"\n')
    tm = _write(tmp_path, "tm.yaml", 'notes: "no version pinned"\n')
    result = validate_threat_model_freshness(cap, tm)
    assert result.ok is False
    assert result.threat_model_manifest_version is None


def test_current_repo_manifest_and_threat_model_are_in_sync():
    # Regression: config/capability-manifest.yaml and config/threat-model.yaml
    # ship together and must already agree - this is what CI enforces on every
    # change to either file (.github/workflows/ai-tm-freshness.yml).
    result = validate_threat_model_freshness(
        "config/capability-manifest.yaml", "config/threat-model.yaml"
    )
    assert result.ok is True, result.reason


def test_cli_exit_code(tmp_path, capsys):
    import sys

    from agent_plane import authority_cli

    cap = _write(tmp_path, "cap.yaml", 'version: "1"\n')
    tm = _write(tmp_path, "tm.yaml", 'capability_manifest_version: "2"\n')

    try:
        authority_cli.main(["check-freshness", "--capabilities", cap, "--threat-model", tm])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected SystemExit(2)")
    assert "FAIL" in capsys.readouterr().out
