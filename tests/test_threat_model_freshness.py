from agent_plane.authority.validator import validate_threat_model_freshness


def test_threat_model_must_reference_current_manifest(tmp_path):
    capability = tmp_path / "capabilities.yaml"
    threat = tmp_path / "threat-model.yaml"
    capability.write_text('version: "42"\n', encoding="utf-8")
    threat.write_text('capability_manifest_version: "41"\n', encoding="utf-8")

    result = validate_threat_model_freshness(str(capability), str(threat))

    assert result.ok is False
    assert result.manifest_version == "42"
    assert result.threat_model_manifest_version == "41"


def test_current_threat_model_passes(tmp_path):
    capability = tmp_path / "capabilities.yaml"
    threat = tmp_path / "threat-model.yaml"
    capability.write_text('version: "42"\n', encoding="utf-8")
    threat.write_text('capability_manifest_version: "42"\n', encoding="utf-8")

    result = validate_threat_model_freshness(str(capability), str(threat))

    assert result.ok is True
