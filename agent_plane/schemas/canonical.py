"""Provider-agnostic canonical types shared across the data plane.

A request is normalized into a ``CanonicalAIRequest`` so that policy is portable
across model providers. The Policy Decision Point returns a ``Decision`` object
(not a boolean) carrying routing, obligations, and audit metadata.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DataClassification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    REGULATED = "regulated"


# Severity order, lowest -> highest. Used so a derived classification can only
# *escalate* the caller-supplied one, never lower it.
_CLASSIFICATION_RANK = {
    DataClassification.PUBLIC: 0,
    DataClassification.INTERNAL: 1,
    DataClassification.CONFIDENTIAL: 2,
    DataClassification.REGULATED: 3,
}


def max_classification(
    a: DataClassification, b: DataClassification
) -> DataClassification:
    """Return the more sensitive of two classifications."""
    return a if _CLASSIFICATION_RANK[a] >= _CLASSIFICATION_RANK[b] else b


class DecisionAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


class LogLevel(str, Enum):
    METADATA_ONLY = "metadata_only"
    FULL = "full"


class Actor(BaseModel):
    """Resolved identity for a user-app-agent chain."""

    user_id: str
    tenant: str = "default"
    department: str | None = None
    app_id: str | None = None
    agent_id: str | None = None
    clearance: DataClassification = DataClassification.INTERNAL
    allowed_tools: list[str] = Field(default_factory=list)
    # Group/role memberships, used for document-level ACLs in RAG authorization.
    groups: list[str] = Field(default_factory=list)


class CanonicalAIRequest(BaseModel):
    request_type: str = "chat_completion"
    messages: list[dict[str, Any]] = Field(default_factory=list)
    model_requested: str
    estimated_tokens: int = 0
    data_classification: DataClassification = DataClassification.INTERNAL
    # Function/tool names the request declares (OpenAI ``tools`` block).
    tools_requested: list[str] = Field(default_factory=list)
    stream: bool = False
    actor: Actor


class Decision(BaseModel):
    """The output of the Policy Decision Point.

    More than allow/deny: it carries the route, transformation obligations, and
    the audit fingerprint that proves *why* a request was permitted.
    """

    decision: DecisionAction = DecisionAction.ALLOW
    reason: str | None = None
    route: str | None = None
    max_tokens: int | None = None
    redact: list[str] = Field(default_factory=list)
    log_level: LogLevel = LogLevel.METADATA_ONLY
    requires_human_approval: bool = False
    obligations: list[str] = Field(default_factory=list)
    cache_ttl: int | None = None
    # Tools the actor requested but is not permitted to use (least privilege).
    denied_tools: list[str] = Field(default_factory=list)

    # Audit fingerprint (populated by the engine).
    rules_matched: list[str] = Field(default_factory=list)
    policy_version: str | None = None
    decision_id: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision == DecisionAction.ALLOW
