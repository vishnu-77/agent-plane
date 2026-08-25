from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AuthorityRule(BaseModel):
    agent_id: str | None = None
    task: str | None = None
    tool: str
    environments: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    max_amount: float | None = None
    require_approval: bool = False


class AuthorityManifest(BaseModel):
    version: str
    rules: list[AuthorityRule] = Field(default_factory=list)


class AuthorityContext(BaseModel):
    task: str | None = None
    environment: str | None = None
    resource: str | None = None
    amount: float | None = None
    approved: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuthorityDecision(BaseModel):
    decision: Literal["allow", "deny", "approval_required"]
    reason: str
    manifest_version: str
    rule_index: int | None = None
