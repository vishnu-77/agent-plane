"""Authority Lease store.

Two implementations share one interface:

* :class:`LeaseStore` - in-memory, single-process. Used by unit tests and by
  ``AUTHORITY_STORE=memory``. Leases and use counters do not survive a restart
  and are not shared between workers.
* :class:`SqlLeaseStore` - SQLAlchemy-backed (SQLite by default, Postgres via
  ``STORAGE_BACKEND=postgres``). The default. Runtime-issued leases,
  revocations, shrinks, and per-action use counters persist and are visible to
  every replica sharing the database. Use reservation is one atomic
  ``UPDATE ... WHERE count < limit`` so two replicas cannot both take the last
  use. On Postgres, :meth:`SqlLeaseStore.transaction` additionally takes a
  transaction-scoped advisory lock so admission is serialized across
  processes, matching the audit chain's approach.

Both also expose a small *request ledger* used by the MCP gateway to
deduplicate retried tool calls by request key.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Integer,
    String,
    create_engine,
    delete,
    func,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from agent_plane.authority.lease import AuthorityLease, parse_lease
from agent_plane.config import Settings

_DEFAULT_LEASES_FILE = "config/leases.yaml"
# Stable key for the Postgres advisory lock that serializes lease admission.
_LEASE_LOCK_KEY = 0x4147505F4C455345 & 0x7FFFFFFFFFFFFFFF  # "AGP_LESE"

RequestKey = tuple[str, str, str]  # (tenant, agent, request_key)


# --------------------------------------------------------------------------- #
# In-memory (single process)
# --------------------------------------------------------------------------- #
class LeaseStore:
    durable = False

    def __init__(self, leases: list[AuthorityLease] | None = None):
        self._leases: dict[str, AuthorityLease] = {lease.id: lease for lease in (leases or [])}
        self._usage: dict[tuple[str, str], int] = {}
        self._requests: dict[RequestKey, tuple[str, dict | None]] = {}
        self._lock = threading.RLock()

    @contextmanager
    def transaction(self):
        """Serialize admission with lease mutations within this single process."""
        with self._lock:
            yield self

    def add(self, lease: AuthorityLease) -> None:
        with self._lock:
            self._leases[lease.id] = lease

    def revoke(self, lease_id: str) -> bool:
        """Mark a lease revoked in place (usage counters, keyed by lease_id,
        are untouched). Returns False if the lease doesn't exist."""
        with self._lock:
            lease = self._leases.get(lease_id)
            if lease is None:
                return False
            self._leases[lease_id] = lease.model_copy(update={"revoked": True})
            return True

    def get(self, lease_id: str) -> AuthorityLease | None:
        return self._leases.get(lease_id)

    def list(self) -> list[AuthorityLease]:
        return list(self._leases.values())

    def for_subject_task(self, subject: str, task: str, tenant: str = "default") -> list[AuthorityLease]:
        return [
            lease for lease in self._leases.values()
            if lease.subject == subject and lease.task == task and lease.tenant == tenant
        ]

    def use_count(self, lease_id: str, action: str) -> int:
        return self._usage.get((lease_id, action), 0)

    def try_consume(self, lease_id: str, action: str, limit: int | None) -> bool:
        """Atomically check-and-increment a per-lease/action usage counter.

        Returns False (without incrementing) once ``limit`` is reached.
        """
        with self._lock:
            key = (lease_id, action)
            count = self._usage.get(key, 0)
            if limit is not None and count >= limit:
                return False
            self._usage[key] = count + 1
            return True

    # -- request ledger (gateway request-key deduplication) ------------------ #
    def request_lookup(self, key: RequestKey) -> tuple[str, dict | None] | None:
        return self._requests.get(key)

    def request_reserve(self, key: RequestKey, digest: str, capacity: int) -> None:
        with self._lock:
            if len(self._requests) >= capacity:
                raise ValueError(
                    "Request key capacity reached; restart only after reconciling outcomes"
                )
            self._requests[key] = (digest, None)

    def request_complete(self, key: RequestKey, digest: str, result: dict) -> None:
        with self._lock:
            self._requests[key] = (digest, result)


# --------------------------------------------------------------------------- #
# SQL-backed (durable, shared)
# --------------------------------------------------------------------------- #
class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


class LeaseRow(Base):
    __tablename__ = "authority_leases"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    subject: Mapped[str] = mapped_column(String(200), index=True)
    task: Mapped[str] = mapped_column(String(200), index=True)
    tenant: Mapped[str] = mapped_column(String(128), index=True, default="default")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    document: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class LeaseUseRow(Base):
    __tablename__ = "authority_lease_uses"

    lease_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    action: Mapped[str] = mapped_column(String(200), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


class GatewayRequestRow(Base):
    __tablename__ = "gateway_requests"

    tenant: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class SqlLeaseStore:
    durable = True

    def __init__(self, db_url: str):
        connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        self._engine = create_engine(db_url, connect_args=connect_args, future=True)
        Base.metadata.create_all(self._engine)
        self._migrate()
        self._session_factory = sessionmaker(bind=self._engine, class_=Session)
        self._is_postgres = "postgresql" in db_url
        self._lock = threading.RLock()
        self._local = threading.local()

    def _migrate(self) -> None:
        """Bring a database created by an earlier release up to date.

        0.5 added the tenant column to authority_leases. Existing rows are
        stamped with the tenant recorded in their JSON document (or "default"),
        so leases issued before the upgrade keep working.
        """
        columns = {c["name"] for c in inspect(self._engine).get_columns("authority_leases")}
        if "tenant" in columns:
            return
        with self._engine.begin() as conn:
            conn.execute(text("ALTER TABLE authority_leases ADD COLUMN tenant VARCHAR(128) DEFAULT 'default'"))
            rows = conn.execute(text("SELECT id, document FROM authority_leases")).all()
            for lease_id, document in rows:
                doc = document if isinstance(document, dict) else json.loads(document or "{}")
                conn.execute(text("UPDATE authority_leases SET tenant = :t WHERE id = :i"),
                             {"t": doc.get("tenant") or "default", "i": lease_id})

    # -- transaction ---------------------------------------------------------- #
    @contextmanager
    def transaction(self):
        """Serialize admission across threads (process lock) and, on Postgres,
        across processes (transaction-scoped advisory lock). SQLite serializes
        writers itself and use reservation is a single atomic statement, so a
        multi-process SQLite deployment stays correct, just less concurrent."""
        with self._lock:
            depth = getattr(self._local, "depth", 0)
            self._local.depth = depth + 1
            lock_session: Session | None = None
            try:
                if depth == 0 and self._is_postgres:
                    lock_session = self._session_factory()
                    lock_session.execute(
                        text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LEASE_LOCK_KEY}
                    )
                yield self
            finally:
                self._local.depth = depth
                if lock_session is not None:
                    lock_session.commit()  # releases the advisory lock
                    lock_session.close()

    # -- leases ---------------------------------------------------------------- #
    @staticmethod
    def _to_lease(row: LeaseRow) -> AuthorityLease:
        lease = AuthorityLease.model_validate(row.document)
        if lease.revoked != bool(row.revoked):
            lease = lease.model_copy(update={"revoked": bool(row.revoked)})
        return lease

    def add(self, lease: AuthorityLease) -> None:
        with self._session_factory() as session:
            row = session.get(LeaseRow, lease.id)
            document = lease.model_dump(mode="json")
            if row is None:
                session.add(LeaseRow(id=lease.id, subject=lease.subject, task=lease.task,
                                     tenant=lease.tenant, revoked=lease.revoked, document=document))
            else:
                row.subject, row.task, row.tenant, row.revoked, row.document = (
                    lease.subject, lease.task, lease.tenant, lease.revoked, document)
            session.commit()

    def seed(self, leases: list[AuthorityLease]) -> int:
        """Insert configured leases that are not already stored. Stored copies
        win so a revocation or shrink survives a restart with the same YAML."""
        inserted = 0
        with self._session_factory() as session:
            for lease in leases:
                if session.get(LeaseRow, lease.id) is None:
                    session.add(LeaseRow(id=lease.id, subject=lease.subject, task=lease.task,
                                         tenant=lease.tenant, revoked=lease.revoked,
                                         document=lease.model_dump(mode="json")))
                    inserted += 1
            session.commit()
        return inserted

    def revoke(self, lease_id: str) -> bool:
        with self._session_factory() as session:
            row = session.get(LeaseRow, lease_id)
            if row is None:
                return False
            row.revoked = True
            document = dict(row.document)
            document["revoked"] = True
            row.document = document
            session.commit()
            return True

    def get(self, lease_id: str) -> AuthorityLease | None:
        with self._session_factory() as session:
            row = session.get(LeaseRow, lease_id)
            return self._to_lease(row) if row else None

    def list(self) -> list[AuthorityLease]:
        with self._session_factory() as session:
            rows = session.scalars(select(LeaseRow).order_by(LeaseRow.created_at)).all()
            return [self._to_lease(r) for r in rows]

    def for_subject_task(self, subject: str, task: str, tenant: str = "default") -> list[AuthorityLease]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(LeaseRow).where(LeaseRow.subject == subject, LeaseRow.task == task,
                                       LeaseRow.tenant == tenant)
                .order_by(LeaseRow.created_at)
            ).all()
            return [self._to_lease(r) for r in rows]

    # -- use counters ------------------------------------------------------------ #
    def use_count(self, lease_id: str, action: str) -> int:
        with self._session_factory() as session:
            row = session.get(LeaseUseRow, (lease_id, action))
            return int(row.count) if row else 0

    def try_consume(self, lease_id: str, action: str, limit: int | None) -> bool:
        with self._session_factory() as session:
            if session.get(LeaseUseRow, (lease_id, action)) is None:
                session.add(LeaseUseRow(lease_id=lease_id, action=action, count=0))
                try:
                    session.commit()
                except Exception:  # noqa: BLE001 - concurrent insert of the same row
                    session.rollback()
            stmt = update(LeaseUseRow).where(
                LeaseUseRow.lease_id == lease_id, LeaseUseRow.action == action
            ).values(count=LeaseUseRow.count + 1)
            if limit is not None:
                stmt = stmt.where(LeaseUseRow.count < limit)
            result = session.execute(stmt)
            session.commit()
            return result.rowcount == 1

    # -- request ledger ----------------------------------------------------------- #
    def request_lookup(self, key: RequestKey) -> tuple[str, dict | None] | None:
        with self._session_factory() as session:
            row = session.get(GatewayRequestRow, key)
            return (row.digest, row.result) if row else None

    def request_reserve(self, key: RequestKey, digest: str, capacity: int) -> None:
        tenant, agent, request_key = key
        with self._session_factory() as session:
            total = session.scalar(select(func.count()).select_from(GatewayRequestRow)) or 0
            if int(total) >= capacity:
                raise ValueError("Request key capacity reached; reconcile outcomes before purging")
            session.add(GatewayRequestRow(tenant=tenant, agent=agent, request_key=request_key,
                                          digest=digest, result=None))
            try:
                session.commit()
            except Exception as exc:  # noqa: BLE001 - lost the race to another replica
                session.rollback()
                raise ValueError("Request key already in progress on another replica") from exc

    def request_complete(self, key: RequestKey, digest: str, result: dict) -> None:
        with self._session_factory() as session:
            row = session.get(GatewayRequestRow, key)
            if row is None:
                session.add(GatewayRequestRow(tenant=key[0], agent=key[1], request_key=key[2],
                                              digest=digest, result=result))
            else:
                row.digest, row.result = digest, result
            session.commit()

    def purge_requests(self, older_than: datetime) -> int:
        with self._session_factory() as session:
            result = session.execute(
                delete(GatewayRequestRow).where(GatewayRequestRow.created_at < older_than)
            )
            session.commit()
            return result.rowcount


# --------------------------------------------------------------------------- #
# Loading / factory
# --------------------------------------------------------------------------- #
def load_leases(path: str) -> list[AuthorityLease]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [parse_lease(d) for d in doc.get("leases", [])]


def _configured_leases(settings: Settings) -> list[AuthorityLease]:
    path: str | None = settings.leases_file or (
        _DEFAULT_LEASES_FILE if Path(_DEFAULT_LEASES_FILE).exists() else None
    )
    if path is None:
        from agent_plane.defaults import default_config_file

        default = default_config_file("leases.yaml")
        path = str(default) if default.exists() else None
    return load_leases(path) if path else []


def build_lease_store(settings: Settings) -> Any:
    leases = _configured_leases(settings)
    if settings.authority_store == "memory":
        return LeaseStore(leases)
    store = SqlLeaseStore(settings.audit_db_url)
    store.seed(leases)
    return store
