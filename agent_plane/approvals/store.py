"""Approval request store.

An ``APPROVAL_REQUIRED`` decision on ``POST /v1/authorize`` (or the MCP
gateway) creates one :class:`ApprovalRequest`. An operator approves or rejects
it through ``/v1/approvals``; the executor then resumes by calling
``POST /v1/authorize`` again with ``approval`` set to the request id. An
approved request can be consumed exactly once, and only for the identical
(subject, task, action, resource) it was raised for.

Persistence follows the lease store: SQL (shared by every replica) by default,
in-memory under ``AUTHORITY_STORE=memory``.
"""
from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, String, create_engine, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from agent_plane.config import Settings

ApprovalStatus = Literal["pending", "approved", "rejected", "consumed", "expired"]
_TERMINAL = {"rejected", "consumed", "expired"}


class ApprovalRequest(BaseModel):
    id: str
    tenant: str
    subject: str
    task: str
    action: str
    resource: str
    lease_id: str | None = None
    evidence_id: str
    status: ApprovalStatus = "pending"
    context: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    expires_at: datetime | None = None
    decided_at: datetime | None = None
    decided_by: str | None = None
    note: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status == "pending" and (
            self.expires_at is None or self.expires_at > datetime.now(UTC)
        )


def new_request(
    *, tenant: str, subject: str, task: str, action: str, resource: str,
    lease_id: str | None, evidence_id: str, ttl_seconds: int,
    context: dict[str, str] | None = None, lease_expires_at: datetime | None = None,
) -> ApprovalRequest:
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=ttl_seconds) if ttl_seconds > 0 else None
    if lease_expires_at is not None and (expires is None or lease_expires_at < expires):
        expires = lease_expires_at  # an approval never outlives its lease
    return ApprovalRequest(
        id=f"apr_{uuid.uuid4().hex[:16]}", tenant=tenant, subject=subject, task=task,
        action=action, resource=resource, lease_id=lease_id, evidence_id=evidence_id,
        context=dict(context or {}), created_at=now, expires_at=expires,
    )


def _effective(req: ApprovalRequest) -> ApprovalRequest:
    """Expire on read; the stored status is updated lazily by the store."""
    if req.status == "pending" and req.expires_at is not None and req.expires_at <= datetime.now(UTC):
        return req.model_copy(update={"status": "expired"})
    return req


# --------------------------------------------------------------------------- #
# In-memory
# --------------------------------------------------------------------------- #
class MemoryApprovalStore:
    durable = False

    def __init__(self) -> None:
        self._items: dict[str, ApprovalRequest] = {}
        self._lock = threading.RLock()

    def create(self, req: ApprovalRequest) -> ApprovalRequest:
        with self._lock:
            self._items[req.id] = req
            return req

    def get(self, approval_id: str) -> ApprovalRequest | None:
        req = self._items.get(approval_id)
        return _effective(req) if req else None

    def list(self, *, status: str | None = None, tenant: str | None = None,
             limit: int = 100) -> list[ApprovalRequest]:
        items = [_effective(r) for r in self._items.values()]
        if status:
            items = [r for r in items if r.status == status]
        if tenant:
            items = [r for r in items if r.tenant == tenant]
        items.sort(key=lambda r: r.created_at, reverse=True)
        return items[:limit]

    def decide(self, approval_id: str, *, status: Literal["approved", "rejected"],
               decided_by: str, note: str | None) -> ApprovalRequest | None:
        with self._lock:
            req = self.get(approval_id)
            if req is None or req.status != "pending":
                return req
            req = req.model_copy(update={
                "status": status, "decided_at": datetime.now(UTC),
                "decided_by": decided_by, "note": note,
            })
            self._items[approval_id] = req
            return req

    def consume(self, approval_id: str) -> bool:
        """Atomically move approved -> consumed. False if not currently approved."""
        with self._lock:
            req = self.get(approval_id)
            if req is None or req.status != "approved":
                return False
            self._items[approval_id] = req.model_copy(update={"status": "consumed"})
            return True


# --------------------------------------------------------------------------- #
# SQL
# --------------------------------------------------------------------------- #
class Base(DeclarativeBase):
    pass


class ApprovalRow(Base):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(128), index=True)
    subject: Mapped[str] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    document: Mapped[dict] = mapped_column(JSON)


def _naive(dt: datetime | None) -> datetime | None:
    # SQLite drops tzinfo; store UTC-naive consistently and re-attach on read.
    return dt.astimezone(UTC).replace(tzinfo=None) if dt is not None else None


class SqlApprovalStore:
    durable = True

    def __init__(self, db_url: str):
        connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        self._engine = create_engine(db_url, connect_args=connect_args, future=True)
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine, class_=Session)

    @staticmethod
    def _to_model(row: ApprovalRow) -> ApprovalRequest:
        req = ApprovalRequest.model_validate(row.document)
        if req.status != row.status:
            req = req.model_copy(update={"status": row.status})
        return _effective(req)

    def create(self, req: ApprovalRequest) -> ApprovalRequest:
        with self._session_factory() as session:
            session.add(ApprovalRow(
                id=req.id, tenant=req.tenant, subject=req.subject, status=req.status,
                created_at=_naive(req.created_at), expires_at=_naive(req.expires_at),
                document=req.model_dump(mode="json"),
            ))
            session.commit()
        return req

    def get(self, approval_id: str) -> ApprovalRequest | None:
        with self._session_factory() as session:
            row = session.get(ApprovalRow, approval_id)
            return self._to_model(row) if row else None

    def list(self, *, status: str | None = None, tenant: str | None = None,
             limit: int = 100) -> list[ApprovalRequest]:
        stmt = select(ApprovalRow).order_by(ApprovalRow.created_at.desc())
        if tenant:
            stmt = stmt.where(ApprovalRow.tenant == tenant)
        if status and status != "expired":
            stmt = stmt.where(ApprovalRow.status == status)
        elif status == "expired":
            stmt = stmt.where(ApprovalRow.status.in_(["pending", "expired"]))
        with self._session_factory() as session:
            rows = session.scalars(stmt.limit(limit * 2 if status else limit)).all()
        items = [self._to_model(r) for r in rows]
        if status:
            items = [r for r in items if r.status == status]
        return items[:limit]

    def decide(self, approval_id: str, *, status: Literal["approved", "rejected"],
               decided_by: str, note: str | None) -> ApprovalRequest | None:
        with self._session_factory() as session:
            row = session.get(ApprovalRow, approval_id)
            if row is None:
                return None
            current = self._to_model(row)
            if current.status != "pending":
                return current
            decided = current.model_copy(update={
                "status": status, "decided_at": datetime.now(UTC),
                "decided_by": decided_by, "note": note,
            })
            # Guard against a concurrent decision on another replica.
            result = session.execute(
                update(ApprovalRow).where(ApprovalRow.id == approval_id, ApprovalRow.status == "pending")
                .values(status=status, document=decided.model_dump(mode="json"))
            )
            session.commit()
            return decided if result.rowcount == 1 else self.get(approval_id)

    def consume(self, approval_id: str) -> bool:
        with self._session_factory() as session:
            row = session.get(ApprovalRow, approval_id)
            if row is None:
                return False
            document = dict(row.document)
            document["status"] = "consumed"
            result = session.execute(
                update(ApprovalRow).where(ApprovalRow.id == approval_id, ApprovalRow.status == "approved")
                .values(status="consumed", document=document)
            )
            session.commit()
            return result.rowcount == 1


def build_approval_store(settings: Settings) -> Any:
    if settings.authority_store == "memory":
        return MemoryApprovalStore()
    return SqlApprovalStore(settings.audit_db_url)
