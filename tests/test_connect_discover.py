"""Phase 16: environment auto-discovery. Filesystem probes only - no
network calls, no credential required."""
from agent_plane.connect.discover import discover


def test_discover_finds_claude_code_settings(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}")
    found = discover(cwd=tmp_path, home=tmp_path / "nonexistent-home")
    assert any(d.target == "claude" for d in found)


def test_discover_finds_nothing_in_an_empty_directory(tmp_path):
    found = discover(cwd=tmp_path, home=tmp_path / "nonexistent-home")
    assert found == []


def test_discover_finds_multiple_integrations(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}")
    (tmp_path / ".cursor").mkdir()
    found = discover(cwd=tmp_path, home=tmp_path / "nonexistent-home")
    targets = {d.target for d in found}
    assert targets == {"claude", "cursor"}


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        test_discover_finds_claude_code_settings(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_discover_finds_nothing_in_an_empty_directory(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_discover_finds_multiple_integrations(Path(d))
    print("ok")
