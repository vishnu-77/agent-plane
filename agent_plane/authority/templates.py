"""Lease templates: issue a task-bound lease from a named shape.

Most products issue a handful of lease shapes over and over ("repair one
staging service", "clean stale branches in one repo"). A template fixes the
actions, protected resources, approval requirements, use limits, impact, and
TTL once; the trusted backend supplies only the subject, the task id, and the
few variables that scope the resources.

Variables are substituted into ``{name}`` placeholders in ``resources`` and
``protected_resources``. Values are restricted to a conservative character set
so a caller can never widen a template's scope by injecting a glob (``*``,
``?``, ``[``) or a path traversal.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from string import Formatter
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_plane.authority.lease import AuthorityLease

_DEFAULT_TEMPLATES_FILE = "config/lease-templates.yaml"
_VARIABLE_VALUE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./:@-]{0,199}$")
_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LeaseTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,100}$")
    description: str = ""
    resources: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    protected_resources: list[str] = Field(default_factory=list)
    require_approval: list[str] = Field(default_factory=list)
    max_uses: dict[str, int] = Field(default_factory=dict)
    ttl_seconds: int = Field(default=3600, gt=0, le=30 * 24 * 3600)
    maximum_impact: str = "reversible"
    child_authority: str = "none"

    @model_validator(mode="after")
    def _check_placeholders(self) -> LeaseTemplate:
        for pattern in (*self.resources, *self.protected_resources):
            for _, field, spec, conversion in Formatter().parse(pattern):
                if field is None:
                    continue
                if spec or conversion or not _VARIABLE_NAME.fullmatch(field):
                    raise ValueError(f"placeholder {field!r} must be a plain variable name")
        return self

    @property
    def variables(self) -> list[str]:
        names: list[str] = []
        for pattern in (*self.resources, *self.protected_resources):
            for _, field, _, _ in Formatter().parse(pattern):
                if field is not None and field not in names:
                    names.append(field)
        return names

    def render(
        self, *, subject: str, task: str, tenant: str = "default",
        variables: dict[str, Any] | None = None, lease_id: str | None = None,
    ) -> AuthorityLease:
        supplied = dict(variables or {})
        missing = [v for v in self.variables if v not in supplied]
        if missing:
            raise ValueError(f"missing template variables: {missing}")
        unknown = [k for k in supplied if k not in self.variables]
        if unknown:
            raise ValueError(f"unknown template variables: {unknown}")
        for key, value in supplied.items():
            if not isinstance(value, str) or not _VARIABLE_VALUE.fullmatch(value) or value in (".", ".."):
                raise ValueError(
                    f"variable {key!r} must be a plain resource segment (no globs or traversal)"
                )
        return AuthorityLease(
            id=lease_id or f"lease-{self.name}-{uuid.uuid4().hex[:10]}",
            task=task,
            subject=subject,
            tenant=tenant,
            resources=[r.format_map(supplied) for r in self.resources],
            actions=list(self.actions),
            protected_resources=[p.format_map(supplied) for p in self.protected_resources],
            max_uses=dict(self.max_uses),
            require_approval=list(self.require_approval),
            expires_at=datetime.now(UTC) + timedelta(seconds=self.ttl_seconds),
            maximum_impact=self.maximum_impact,
            child_authority=self.child_authority,
        )


class TemplateCatalog:
    def __init__(self, templates: list[LeaseTemplate] | None = None):
        self._templates = {t.name: t for t in (templates or [])}

    def get(self, name: str) -> LeaseTemplate | None:
        return self._templates.get(name)

    def list(self) -> list[LeaseTemplate]:
        return list(self._templates.values())


def load_templates(path: str) -> list[LeaseTemplate]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    templates = [LeaseTemplate.model_validate(t) for t in doc.get("templates", [])]
    if len({t.name for t in templates}) != len(templates):
        raise ValueError("duplicate lease template names")
    return templates


def build_template_catalog(settings: Any) -> TemplateCatalog:
    path: str | None = getattr(settings, "lease_templates_file", None) or (
        _DEFAULT_TEMPLATES_FILE if Path(_DEFAULT_TEMPLATES_FILE).exists() else None
    )
    if path is None:
        from agent_plane.defaults import default_config_file

        default = default_config_file("lease-templates.yaml")
        path = str(default) if default.exists() else None
    return TemplateCatalog(load_templates(path) if path else [])
