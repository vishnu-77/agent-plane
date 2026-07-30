"""Audit evidence ORM.

One row per request: the decision fingerprint that proves *why* a request was
allowed under which policy version, plus token/latency metadata. Under
``metadata_only`` logging we store a prompt *hash*, never the raw prompt.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Actor
    user_id: Mapped[str] = mapped_column(String(128))
    tenant: Mapped[str] = mapped_column(String(128))
    department: Mapped[str | None] = mapped_column(String(128), nullable=True)
    app_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Request / routing
    model_requested: Mapped[str] = mapped_column(String(128))
    model_used: Mapped[str | None] = mapped_column(String(128), nullable=True)
    data_classification: Mapped[str] = mapped_column(String(32))

    # Decision fingerprint
    decision: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rules_matched: Mapped[list] = mapped_column(JSON, default=list)
    obligations_applied: Mapped[list] = mapped_column(JSON, default=list)
    redactions_applied: Mapped[list] = mapped_column(JSON, default=list)
    log_level: Mapped[str] = mapped_column(String(32), default="metadata_only")

    # Metrics
    estimated_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)

    # Privacy-preserving prompt fingerprint
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Tamper-evidence: hash chain + HMAC signature over the decision record.
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Unique: each chain link is distinct - a duplicate/replayed append is rejected.
    event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
