"""Operator-owned mappings. Model arguments never choose credentials or routes."""
from __future__ import annotations

import json
import re
from pathlib import Path
from string import Formatter
from urllib.parse import urlsplit

import yaml
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Mapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,100}$")
    upstream_tool: str
    action: str
    resource: str
    description: str = "Governed tool; permission is checked for each call."
    input_schema: dict

    @model_validator(mode="after")
    def validate_mapping(self):
        Draft202012Validator.check_schema(self.input_schema)
        # Remote schema resolution and implicit extra arguments are not supported.
        if any(f'"{key}"' in json.dumps(self.input_schema) for key in ("$ref", "$dynamicRef")):
            raise ValueError("Schema references are not supported")
        if self.input_schema.get("type") != "object" or self.input_schema.get("additionalProperties") is not False:
            raise ValueError("Tool schema must be an object with additionalProperties: false")
        for _, field, spec, conversion in Formatter().parse(self.resource):
            if field is not None and (not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field) or spec or conversion):
                raise ValueError("Resource placeholders must be plain argument names")
        return self

    def resolve(self, arguments: dict) -> str:
        Draft202012Validator(self.input_schema).validate(arguments)
        values = {}
        for _, field, _, _ in Formatter().parse(self.resource):
            if field is None:
                continue
            value = arguments.get(field)
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}", value) or value in (".", ".."):
                raise ValueError("Resource argument must be a canonical single path segment")
            values[field] = value
        return self.resource.format_map(values)


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant: str
    agent: str
    task: str
    lease: str


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upstream_url: str
    upstream_secret_env: str | None = None
    timeout_seconds: float = Field(default=10, gt=0, le=60)
    max_concurrency: int = Field(default=8, ge=1, le=64)
    max_response_bytes: int = Field(default=1_000_000, ge=1024, le=4_000_000)
    # Upper bound on retained request keys (deduplication ledger).
    max_request_keys: int = Field(default=10_000, ge=100, le=1_000_000)
    bindings: list[Binding]
    tools: list[Mapping]

    @model_validator(mode="after")
    def validate_config(self):
        url = urlsplit(self.upstream_url)
        if url.username or url.password or url.query or url.fragment or not url.hostname:
            raise ValueError("Upstream must be a fixed URL without credentials, query or fragment")
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError("Upstream requires HTTPS, except explicit localhost mock servers")
        if len({t.name for t in self.tools}) != len(self.tools):
            raise ValueError("Duplicate public tool names")
        if len({(b.tenant, b.agent) for b in self.bindings}) != len(self.bindings):
            raise ValueError("An agent must have exactly one trusted task binding in this preview")
        # Legacy leases have no tenant column: forbid sharing one across bindings.
        if len({b.lease for b in self.bindings}) != len(self.bindings):
            raise ValueError("Each binding needs its own lease")
        return self


def load_config(path: str) -> GatewayConfig:
    return GatewayConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
