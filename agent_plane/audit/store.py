"""Audit store.

SQLAlchemy-backed; SQLite by default, Postgres via STORAGE_BACKEND=postgres.
The gateway depends only on :class:`AuditStore`, so the engine swaps without
touching call sites.
"""
from __future__ import annotations

import threading
from typing import Any, Protocol

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from agent_plane.audit.models import AuditEvent, Base
from agent_plane.audit.signing import sign_event
from agent_plane.config import Settings

# Stable key for the Postgres advisory lock that serializes chain appends.
_AUDIT_CHAIN_LOCK_KEY = 0x4147505F4C4F4720 & 0x7FFFFFFFFFFFFFFF  # "AGP_LOG", masked to bigint


def _normalized(event: dict[str, Any]) -> dict[str, Any]:
    """Fill the full signed column set with model defaults.

    Signing hashes this normalized payload and it is also what gets persisted, so
    verification (which reads the stored row, defaults and all) always matches -
    regardless of which optional fields a caller omitted.
    """
    payload: dict[str, Any] = {
        "decision_id": None, "user_id": None, "tenant": None,
        "department": None, "app_id": None, "agent_id": None,
        "model_requested": None, "model_used": None, "data_classification": None,
        "decision": None, "reason": None, "policy_version": None,
        "rules_matched": [], "obligations_applied": [], "redactions_applied": [],
        "log_level": "metadata_only", "estimated_tokens": 0, "total_tokens": 0,
        "latency_ms": 0, "prompt_hash": None,
    }
    payload.update(event)
    return payload


class AuditStore(Protocol):
    def record(self, event: dict[str, Any]) -> None: ...

    def recent(self, limit: int = 50) -> list[dict[str, Any]]: ...


class SqlAuditStore:
    def __init__(self, db_url: str, signing_key: str = "dev-audit-key-change-me"):
        connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        self._engine = create_engine(db_url, connect_args=connect_args, future=True)
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine, class_=Session)
        self._signing_key = signing_key
        self._is_postgres = "postgresql" in db_url
        # Serializes chain-head read + append *within this process*.
        self._lock = threading.Lock()

    def record(self, event: dict[str, Any]) -> None:
        # The hash chain must have exactly one appender at a time, or two writers
        # could read the same predecessor and fork it. We serialize on two levels:
        #   1) an in-process lock (covers a single worker), and
        #   2) a transaction-scoped Postgres advisory lock (covers many
        #      workers/processes sharing one DB). SQLite already serializes writes.
        with self._lock:
            with self._session_factory() as session:
                if self._is_postgres:
                    session.execute(
                        text("SELECT pg_advisory_xact_lock(:k)"),
                        {"k": _AUDIT_CHAIN_LOCK_KEY},
                    )
                prev_hash = session.scalars(
                    select(AuditEvent.event_hash).order_by(AuditEvent.id.desc()).limit(1)
                ).first() or ""
                payload = _normalized(event)
                event_hash, signature = sign_event(payload, prev_hash, self._signing_key)
                session.add(
                    AuditEvent(
                        **payload,
                        prev_hash=prev_hash or None,
                        event_hash=event_hash,
                        signature=signature,
                    )
                )
                session.commit()  # releases the advisory lock

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
            ).all()
            return [self._to_dict(r) for r in rows]

    @staticmethod
    def _to_dict(row: AuditEvent) -> dict[str, Any]:
        return {
            "decision_id": row.decision_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "user_id": row.user_id,
            "tenant": row.tenant,
            "department": row.department,
            "app_id": row.app_id,
            "agent_id": row.agent_id,
            "model_requested": row.model_requested,
            "model_used": row.model_used,
            "data_classification": row.data_classification,
            "decision": row.decision,
            "reason": row.reason,
            "policy_version": row.policy_version,
            "rules_matched": row.rules_matched,
            "obligations_applied": row.obligations_applied,
            "redactions_applied": row.redactions_applied,
            "log_level": row.log_level,
            "estimated_tokens": row.estimated_tokens,
            "total_tokens": row.total_tokens,
            "latency_ms": row.latency_ms,
            "prompt_hash": row.prompt_hash,
            "prev_hash": row.prev_hash,
            "event_hash": row.event_hash,
            "signature": row.signature,
        }


def build_audit_store(settings: Settings) -> AuditStore:
    return SqlAuditStore(settings.audit_db_url, settings.audit_signing_key)
