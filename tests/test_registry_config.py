"""Config-driven model registry."""
from __future__ import annotations

from agent_plane.routing.registry import _DEFAULT_MODELS, load_model_entries


def test_load_models_from_yaml(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text(
        "models:\n"
        "  - id: my-model\n"
        "    provider: openai\n"
        "    upstream_model: gpt-x\n"
        "    tags: [openai, external]\n"
        "    fallback: [backup-model]\n",
        encoding="utf-8",
    )
    entries = load_model_entries(str(path))
    assert len(entries) == 1
    e = entries[0]
    assert e.model_id == "my-model"
    assert e.provider == "openai"
    assert e.upstream_model == "gpt-x"
    assert {"openai", "external"} <= set(e.tags)
    assert e.fallback == ("backup-model",)


def test_shipped_catalog_mirrors_builtin_defaults():
    # Guards against drift between config/models.yaml and the in-code fallback.
    entries = load_model_entries("config/models.yaml")
    assert {e.model_id for e in entries} == {m.model_id for m in _DEFAULT_MODELS}
