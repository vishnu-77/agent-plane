"""Pydantic models describing the YAML policy DSL.

A policy file has a ``name``, ``version``, an optional ``scope``, a ``match``
block, a ``decision`` block, and optional top-level ``obligations`` /
``exceptions``. The engine evaluates rules and produces a runtime Decision.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PolicyScope(BaseModel):
    """Coarse pre-filter: which tenants/departments a policy applies to."""

    tenants: list[str] | None = None
    departments: list[str] | None = None


class PolicyMatch(BaseModel):
    """Conditions evaluated against the canonical request + actor.

    All present fields must match (AND). List fields match if the request value
    is contained in the list. ``None`` fields are ignored.
    """

    request_type: str | None = None
    data_classification: list[str] | None = None
    model_provider: list[str] | None = None
    model_requested: list[str] | None = None
    actor_type: str | None = None  # "user" | "agent"
    departments: list[str] | None = None
    # Match when the request declares any of these tools (per-tool policy).
    tools: list[str] | None = None


class PolicyDecisionSpec(BaseModel):
    """The decision a rule yields when it matches."""

    action: str = "allow"  # allow | deny | approval_required | allow_with_filters
    reason: str | None = None
    route: str | None = None
    max_tokens: int | None = None
    redact: list[str] = Field(default_factory=list)
    log_level: str | None = None
    cache_ttl: int | None = None
    obligations: list[str] = Field(default_factory=list)
    # Free-form extras (e.g. approval block, filters) kept for audit/extension.
    extra: dict[str, Any] = Field(default_factory=dict)


class PolicyException(BaseModel):
    """An override that flips a deny into a constrained allow (or vice versa)."""

    model_provider: str | None = None
    model_requested: str | None = None
    action: str = "allow"
    obligations: list[str] = Field(default_factory=list)


class Policy(BaseModel):
    name: str
    version: str
    scope: PolicyScope = Field(default_factory=PolicyScope)
    match: PolicyMatch = Field(default_factory=PolicyMatch)
    decision: PolicyDecisionSpec
    obligations: list[str] = Field(default_factory=list)
    exceptions: list[PolicyException] = Field(default_factory=list)
