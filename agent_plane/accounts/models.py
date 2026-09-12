"""Account records. Public shapes (pydantic) and their storage rows."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import JSON, Boolean, DateTime, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Role = Literal["owner", "admin", "member"]
Mode = Literal["observe", "govern", "enforce"]
Environment = Literal["live", "test", "mgmt"]

# What a connector can actually do, declared per integration kind. The UI must
# never imply agent-plane blocks something a connector cannot block.
IntegrationKind = Literal["claude-code", "codex", "cursor", "mcp", "langgraph", "gateway", "custom"]

INTEGRATION_CATALOG: dict[str, dict[str, Any]] = {
    "claude-code": {
        "label": "Claude Code",
        "observation": "full",
        "enforcement": "partial",
        "summary": "Session, tool calls, files and repositories touched.",
        "enforcement_note": "Pre-tool hooks can block most actions; a tool that runs outside the hook is observed, not stopped.",
        "connect": "agentplane connect claude --key {key}",
    },
    "codex": {
        "label": "Codex",
        "observation": "full",
        "enforcement": "partial",
        "summary": "Session, command and file activity from the Codex CLI.",
        "enforcement_note": "Blocks where the CLI exposes a pre-execution hook; otherwise advisory.",
        "connect": "agentplane connect codex --key {key}",
    },
    "cursor": {
        "label": "Cursor / OpenCode",
        "observation": "partial",
        "enforcement": "advisory",
        "summary": "Activity reported by the editor's agent runtime.",
        "enforcement_note": "Reports what happened; it cannot interrupt the editor.",
        "connect": "agentplane connect cursor --key {key}",
    },
    "mcp": {
        "label": "MCP server",
        "observation": "full",
        "enforcement": "full",
        "summary": "Every tools/list and tools/call passes through agent-plane.",
        "enforcement_note": "agent-plane holds the upstream credential and is the execution chokepoint.",
        "connect": "agentplane connect mcp --key {key} --upstream {upstream}",
    },
    "langgraph": {
        "label": "LangGraph",
        "observation": "application_defined",
        "enforcement": "advisory",
        "summary": "Whatever your graph reports through the SDK.",
        "enforcement_note": "Your code decides whether to honour a decision.",
        "connect": "export AGENTPLANE_API_KEY={key}",
    },
    "gateway": {
        "label": "API / model gateway",
        "observation": "full",
        "enforcement": "full",
        "summary": "OpenAI-compatible and brokered tool traffic routed through agent-plane.",
        "enforcement_note": "agent-plane holds the provider credential and forwards only approved traffic.",
        "connect": "base_url = {base_url}/v1",
    },
    "custom": {
        "label": "Custom agent",
        "observation": "application_defined",
        "enforcement": "advisory",
        "summary": "Actions your application reports and authorizes through the SDK.",
        "enforcement_note": "Your executor decides whether to honour a decision.",
        "connect": "export AGENTPLANE_API_KEY={key}",
    },
}

# Collected by default: metadata that answers "who did what to which resource".
# Content is opt-in, per project, and off unless a human turns it on.
DEFAULT_COLLECTION: dict[str, bool] = {
    "agent_identity": True,
    "integration_identity": True,
    "session_metadata": True,
    "task_metadata": True,
    "action_name": True,
    "resource_identifier": True,
    "decision_result": True,
    "authority_metadata": True,
    "consequence_metadata": True,
    # optional, default off
    "prompt_content": False,
    "tool_arguments": False,
    "tool_output": False,
    "file_content": False,
    "model_messages": False,
}
OPTIONAL_COLLECTION = ("prompt_content", "tool_arguments", "tool_output", "file_content", "model_messages")


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Public records
# --------------------------------------------------------------------------- #
class User(BaseModel):
    id: str
    email: str
    name: str = ""
    created_at: datetime
    last_login_at: datetime | None = None


class Workspace(BaseModel):
    id: str
    name: str
    slug: str
    created_at: datetime
    created_by: str


class Membership(BaseModel):
    workspace_id: str
    user_id: str
    role: Role = "member"
    created_at: datetime


class Project(BaseModel):
    id: str
    workspace_id: str
    name: str
    slug: str
    mode: Mode = "observe"
    collection: dict[str, bool] = Field(default_factory=lambda: dict(DEFAULT_COLLECTION))
    created_at: datetime
    created_by: str
    demo: bool = False

    def collects(self, field: str) -> bool:
        return bool(self.collection.get(field, DEFAULT_COLLECTION.get(field, False)))


class ApiKey(BaseModel):
    id: str
    project_id: str
    name: str
    prefix: str
    last4: str
    environment: Environment = "live"
    scopes: list[str] = Field(default_factory=lambda: ["ingest", "authorize"])
    created_at: datetime
    created_by: str
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    @property
    def masked(self) -> str:
        return f"{self.prefix}{'•' * 12}{self.last4}"

    @property
    def status(self) -> str:
        if self.revoked_at:
            return "revoked"
        if self.expires_at and self.expires_at <= _utcnow():
            return "expired"
        return "active"


class Integration(BaseModel):
    id: str
    project_id: str
    kind: str
    name: str
    host: str | None = None
    status: str = "pending"          # pending -> connected, on the first reported action
    created_at: datetime
    last_seen_at: datetime | None = None
    agents: list[str] = Field(default_factory=list)
    actions: int = 0
    config: dict[str, Any] = Field(default_factory=dict)

    @property
    def catalog(self) -> dict[str, Any]:
        return INTEGRATION_CATALOG.get(self.kind, INTEGRATION_CATALOG["custom"])

    @property
    def observation(self) -> str:
        return str(self.catalog["observation"])

    @property
    def enforcement(self) -> str:
        return str(self.catalog["enforcement"])


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "accounts_users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WorkspaceRow(Base):
    __tablename__ = "accounts_workspaces"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    created_by: Mapped[str] = mapped_column(String(64))


class MembershipRow(Base):
    __tablename__ = "accounts_memberships"
    workspace_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ProjectRow(Base):
    __tablename__ = "accounts_projects"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(200), index=True)
    mode: Mapped[str] = mapped_column(String(16), default="observe")
    collection: Mapped[dict] = mapped_column(JSON, default=dict)
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    created_by: Mapped[str] = mapped_column(String(64))


class ApiKeyRow(Base):
    __tablename__ = "accounts_api_keys"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200))
    prefix: Mapped[str] = mapped_column(String(16))
    last4: Mapped[str] = mapped_column(String(8))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    environment: Mapped[str] = mapped_column(String(8), default="live")
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class IntegrationRow(Base):
    __tablename__ = "accounts_integrations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    host: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    agents: Mapped[list] = mapped_column(JSON, default=list)
    actions: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
