"""Usage metering store.

One metered row per billable action (a model call or a tool call), keyed by
tenant. This is the foundation for usage-based billing: the data needed to charge
later is captured now. Pricing/invoicing is intentionally *not* here - only the
meter. Shares the audit database (SQLite by default, Postgres when configured).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import DateTime, Integer, String, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from agent_plane.config import Settings


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    tenant: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str] = mapped_column(String(128))
    edge: Mapped[str] = mapped_column(String(32))        # "model" | "tool"
    resource: Mapped[str] = mapped_column(String(128))    # model id or tool name
    units: Mapped[int] = mapped_column(Integer, default=0)  # tokens (model) or 1 (tool call)
    calls: Mapped[int] = mapped_column(Integer, default=1)
    decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class UsageStore(Protocol):
    def record(self, event: dict[str, Any]) -> None: ...

    def summary(self, tenant: str, since: datetime | None = None) -> list[dict[str, Any]]: ...


class SqlUsageStore:
    def __init__(self, db_url: str):
        connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        self._engine = create_engine(db_url, connect_args=connect_args, future=True)
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine, class_=Session)

    def record(self, event: dict[str, Any]) -> None:
        with self._session_factory() as session:
            session.add(UsageEvent(**event))
            session.commit()

    def summary(self, tenant: str, since: datetime | None = None) -> list[dict[str, Any]]:
        stmt = (
            select(
                UsageEvent.edge,
                UsageEvent.resource,
                func.sum(UsageEvent.calls),
                func.sum(UsageEvent.units),
            )
            .where(UsageEvent.tenant == tenant)
            .group_by(UsageEvent.edge, UsageEvent.resource)
        )
        if since is not None:
            stmt = stmt.where(UsageEvent.created_at >= since)
        with self._session_factory() as session:
            rows = session.execute(stmt).all()
        return [
            {"edge": edge, "resource": resource, "calls": int(calls or 0), "units": int(units or 0)}
            for edge, resource, calls, units in rows
        ]


class NullUsageStore:
    """Used when metering is disabled."""

    def record(self, event: dict[str, Any]) -> None:  # noqa: D401
        return None

    def summary(self, tenant: str, since: datetime | None = None) -> list[dict[str, Any]]:
        return []


def build_usage_store(settings: Settings) -> UsageStore:
    if not settings.usage_metering:
        return NullUsageStore()
    return SqlUsageStore(settings.audit_db_url)
