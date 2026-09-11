"""`agentplane mcp discover` output must load as a valid gateway config."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

pytest.importorskip("jsonschema")

from agent_plane.enforcement.discover import mapping_for, render
from agent_plane.enforcement.mapping import GatewayConfig


def _tool(name, schema, description="does a thing"):
    return SimpleNamespace(name=name, input_schema=schema, description=description)


def test_mapping_tightens_schema_and_picks_resource_argument():
    mapping, reason = mapping_for(
        "delete_branch",
        {"type": "object", "properties": {"branch": {"type": "string"}, "force": {"type": "boolean"}},
         "required": ["branch"]},
        "Delete a branch", prefix="repo", resource_prefix="github://acme/repo/branches")
    assert reason is None
    assert mapping["name"] == "repo.delete_branch"
    assert mapping["action"] == "repo.delete_branch"
    assert mapping["upstream_tool"] == "delete_branch"
    assert mapping["resource"] == "github://acme/repo/branches/{branch}"
    assert mapping["input_schema"]["additionalProperties"] is False


def test_mapping_without_string_argument_uses_tool_name():
    mapping, _ = mapping_for("list_branches", {"type": "object", "properties": {}}, None,
                             prefix="", resource_prefix="repo")
    assert mapping["resource"] == "repo/list_branches"
    assert mapping["name"] == "list_branches"


def test_unsafe_schemas_are_skipped_with_reason():
    assert mapping_for("t", {"type": "string"}, None, prefix="", resource_prefix="r")[1]
    assert mapping_for("t", {"type": "object", "properties": {"a": {"$ref": "#/x"}}}, None,
                       prefix="", resource_prefix="r")[1]


def test_render_produces_loadable_gateway_config(tmp_path):
    tools = [
        _tool("list_branches", {"type": "object", "properties": {}}),
        _tool("delete_branch", {"type": "object", "properties": {"branch": {"type": "string"}},
                                "required": ["branch"]}),
        _tool("weird", {"type": "array"}),
    ]
    text = render(tools, upstream_url="https://mcp.example.com/mcp", secret_env="UPSTREAM_TOKEN",
                  tenant="acme", agent="repo-agent", task="cleanup", lease="lease-cleanup",
                  prefix="repo", resource_prefix="github://acme/repo/branches")
    assert "REVIEW BEFORE USE" in text
    assert "skipped weird" in text
    config = GatewayConfig.model_validate(yaml.safe_load(text))
    assert [t.name for t in config.tools] == ["repo.list_branches", "repo.delete_branch"]
    assert config.bindings[0].lease == "lease-cleanup"
    assert config.upstream_secret_env == "UPSTREAM_TOKEN"
    # The generated resource template resolves like any operator-written one.
    assert config.tools[1].resolve({"branch": "stale"}) == "github://acme/repo/branches/stale"
