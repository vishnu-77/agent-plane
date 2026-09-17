"""Durable context registry and immutable context snapshots."""
from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from agent_plane.config import Settings
from agent_plane.context.models import (
    ContextAsset,
    ContextLineage,
    ContextRiskVector,
    ContextSnapshot,
    default_risk,
    fingerprint_asset,
    stable_asset_id,
    utcnow,
)
from agent_plane.storage import create_sql_engine


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


def _asset_from_raw(tenant: str, raw: dict[str, Any], *, agent: str | None = None,
                    task: str | None = None, previous: ContextAsset | None = None) -> ContextAsset:
    kind = str(raw.get("kind") or "other").lower()
    if kind not in {"instruction", "skill", "tool", "mcp", "knowledge", "memory", "harness", "event", "external", "other"}:
        kind = "other"
    source = str(raw.get("source") or raw.get("name") or kind)[:500]
    name = str(raw.get("name") or source)[:300]
    asset_id = str(raw.get("id") or stable_asset_id(kind, source))[:128]
    digest = fingerprint_asset(raw)
    now = utcnow()
    trust = str(raw.get("trust") or "unknown")
    if trust not in {"verified", "project-bound", "repository", "internal", "external", "unknown"}:
        trust = "unknown"
    influence = str(raw.get("influence") or "medium")
    if influence not in {"low", "medium", "high"}:
        influence = "medium"
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    provenance = raw.get("provenance") if isinstance(raw.get("provenance"), dict) else {}
    capabilities = sorted({str(v)[:200] for v in (raw.get("capabilities") or []) if v})[:100]
    risk_raw = raw.get("risk") if isinstance(raw.get("risk"), dict) else None
    risk = ContextRiskVector.model_validate(risk_raw) if risk_raw else default_risk(kind, trust, influence, metadata)

    agents = list(previous.agents) if previous else []
    tasks = list(previous.tasks) if previous else []
    for value, target in ((agent, agents), (task, tasks)):
        if value and value not in target:
            target.append(value[:200])

    changed = previous is not None and previous.digest != digest
    return ContextAsset(
        id=asset_id,
        tenant=tenant,
        kind=kind,  # type: ignore[arg-type]
        name=name,
        source=source,
        digest=digest,
        previous_digest=previous.digest if changed else (previous.previous_digest if previous else None),
        version=(previous.version + 1) if changed else (previous.version if previous else 1),
        change_count=(previous.change_count + 1) if changed else (previous.change_count if previous else 0),
        trust=trust,  # type: ignore[arg-type]
        influence=influence,  # type: ignore[arg-type]
        capabilities=capabilities or (list(previous.capabilities) if previous else []),
        agents=agents,
        tasks=tasks,
        provenance=provenance or (dict(previous.provenance) if previous else {}),
        metadata=metadata or (dict(previous.metadata) if previous else {}),
        risk=risk,
        first_seen=previous.first_seen if previous else now,
        last_seen=now,
        changed_at=now if changed or previous is None else previous.changed_at,
    )


class MemoryContextStore:
    durable = False

    def __init__(self) -> None:
        self._assets: dict[tuple[str, str], ContextAsset] = {}
        self._snapshots: dict[tuple[str, str, str], ContextSnapshot] = {}
        self._lineage: dict[tuple[str, str], ContextLineage] = {}
        self._lock = threading.RLock()

    def register_many(self, tenant: str, assets: list[dict[str, Any]], *, agent: str | None = None,
                      task: str | None = None) -> list[ContextAsset]:
        out: list[ContextAsset] = []
        with self._lock:
            for raw in assets[:100]:
                if not isinstance(raw, dict):
                    continue
                key_id = str(raw.get("id") or stable_asset_id(str(raw.get("kind") or "other"),
                                                               str(raw.get("source") or raw.get("name") or "context")))[:128]
                previous = self._assets.get((tenant, key_id))
                rec = _asset_from_raw(tenant, raw, agent=agent, task=task, previous=previous)
                self._assets[(tenant, rec.id)] = rec
                snap_key = (tenant, rec.id, rec.digest)
                if snap_key not in self._snapshots:
                    self._snapshots[snap_key] = ContextSnapshot(
                        tenant=tenant, asset_id=rec.id, digest=rec.digest, observed_at=rec.last_seen,
                        document=rec.public(),
                    )
                out.append(rec)
        return out

    def assets(self, tenant: str | None, *, kind: str | None = None, limit: int = 500) -> list[ContextAsset]:
        items = [a for (t, _), a in self._assets.items() if tenant is None or t == tenant]
        if kind:
            items = [a for a in items if a.kind == kind]
        return sorted(items, key=lambda a: a.last_seen, reverse=True)[:limit]

    def asset(self, tenant: str, asset_id: str) -> ContextAsset | None:
        return self._assets.get((tenant, asset_id))

    def snapshots(self, tenant: str, asset_id: str, limit: int = 50) -> list[ContextSnapshot]:
        items = [s for (t, aid, _), s in self._snapshots.items() if t == tenant and aid == asset_id]
        return sorted(items, key=lambda s: s.observed_at, reverse=True)[:limit]

    def changes(self, tenant: str | None, limit: int = 100) -> list[ContextAsset]:
        return [a for a in self.assets(tenant, limit=1000) if a.change_count > 0][:limit]

    def link_decision(self, tenant: str, decision_id: str, asset_ids: list[str], *, task: str | None = None,
                      agent: str | None = None) -> ContextLineage:
        ids = list(dict.fromkeys(asset_ids))[:100]
        rec = ContextLineage(tenant=tenant, decision_id=decision_id, asset_ids=ids,
                             task=task, agent=agent, created_at=utcnow())
        with self._lock:
            self._lineage[(tenant, decision_id)] = rec
        return rec

    def lineage(self, tenant: str, decision_id: str) -> ContextLineage | None:
        return self._lineage.get((tenant, decision_id))

    def reset_tenant(self, tenant: str) -> None:
        with self._lock:
            for mapping in (self._assets, self._snapshots, self._lineage):
                for key in [k for k in mapping if k[0] == tenant]:
                    del mapping[key]


class Base(DeclarativeBase):
    pass


class AssetRow(Base):
    __tablename__ = "context_assets"
    tenant: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    document: Mapped[dict] = mapped_column(JSON)


class SnapshotRow(Base):
    __tablename__ = "context_snapshots"
    tenant: Mapped[str] = mapped_column(String(128), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    digest: Mapped[str] = mapped_column(String(128), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    document: Mapped[dict] = mapped_column(JSON)


class LineageRow(Base):
    __tablename__ = "context_lineage"
    tenant: Mapped[str] = mapped_column(String(128), primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    document: Mapped[dict] = mapped_column(JSON)


class SqlContextStore:
    durable = True

    def __init__(self, db_url: str) -> None:
        self._engine = create_sql_engine(db_url)
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine, class_=Session)
        self._lock = threading.RLock()

    def register_many(self, tenant: str, assets: list[dict[str, Any]], *, agent: str | None = None,
                      task: str | None = None) -> list[ContextAsset]:
        out: list[ContextAsset] = []
        with self._lock, self._session_factory() as session:
            for raw in assets[:100]:
                if not isinstance(raw, dict):
                    continue
                key_id = str(raw.get("id") or stable_asset_id(str(raw.get("kind") or "other"),
                                                               str(raw.get("source") or raw.get("name") or "context")))[:128]
                row = session.get(AssetRow, (tenant, key_id))
                previous = ContextAsset.model_validate(row.document) if row else None
                rec = _asset_from_raw(tenant, raw, agent=agent, task=task, previous=previous)
                doc = rec.model_dump(mode="json")
                if row is None:
                    session.add(AssetRow(tenant=tenant, id=rec.id, updated_at=_naive(rec.last_seen), document=doc))
                else:
                    row.updated_at, row.document = _naive(rec.last_seen), doc
                snap = session.get(SnapshotRow, (tenant, rec.id, rec.digest))
                if snap is None:
                    session.add(SnapshotRow(
                        tenant=tenant, asset_id=rec.id, digest=rec.digest, observed_at=_naive(rec.last_seen),
                        document=ContextSnapshot(tenant=tenant, asset_id=rec.id, digest=rec.digest,
                                                 observed_at=rec.last_seen, document=rec.public()).model_dump(mode="json"),
                    ))
                out.append(rec)
            session.commit()
        return out

    def assets(self, tenant: str | None, *, kind: str | None = None, limit: int = 500) -> list[ContextAsset]:
        with self._session_factory() as session:
            stmt = select(AssetRow).order_by(AssetRow.updated_at.desc()).limit(limit)
            if tenant is not None:
                stmt = stmt.where(AssetRow.tenant == tenant)
            rows = session.scalars(stmt).all()
            items = [ContextAsset.model_validate(r.document) for r in rows]
            if kind:
                items = [a for a in items if a.kind == kind]
            return items[:limit]

    def asset(self, tenant: str, asset_id: str) -> ContextAsset | None:
        with self._session_factory() as session:
            row = session.get(AssetRow, (tenant, asset_id))
            return ContextAsset.model_validate(row.document) if row else None

    def snapshots(self, tenant: str, asset_id: str, limit: int = 50) -> list[ContextSnapshot]:
        with self._session_factory() as session:
            stmt = (select(SnapshotRow)
                    .where(SnapshotRow.tenant == tenant, SnapshotRow.asset_id == asset_id)
                    .order_by(SnapshotRow.observed_at.desc()).limit(limit))
            return [ContextSnapshot.model_validate(r.document) for r in session.scalars(stmt).all()]

    def changes(self, tenant: str | None, limit: int = 100) -> list[ContextAsset]:
        return [a for a in self.assets(tenant, limit=1000) if a.change_count > 0][:limit]

    def link_decision(self, tenant: str, decision_id: str, asset_ids: list[str], *, task: str | None = None,
                      agent: str | None = None) -> ContextLineage:
        rec = ContextLineage(tenant=tenant, decision_id=decision_id,
                             asset_ids=list(dict.fromkeys(asset_ids))[:100], task=task, agent=agent,
                             created_at=utcnow())
        with self._session_factory() as session:
            row = session.get(LineageRow, (tenant, decision_id))
            doc = rec.model_dump(mode="json")
            if row is None:
                session.add(LineageRow(tenant=tenant, decision_id=decision_id,
                                       created_at=_naive(rec.created_at), document=doc))
            else:
                row.created_at, row.document = _naive(rec.created_at), doc
            session.commit()
        return rec

    def lineage(self, tenant: str, decision_id: str) -> ContextLineage | None:
        with self._session_factory() as session:
            row = session.get(LineageRow, (tenant, decision_id))
            return ContextLineage.model_validate(row.document) if row else None

    def reset_tenant(self, tenant: str) -> None:
        with self._session_factory() as session:
            for model in (AssetRow, SnapshotRow, LineageRow):
                rows = session.scalars(select(model).where(model.tenant == tenant)).all()
                for row in rows:
                    session.delete(row)
            session.commit()


def build_context_store(settings: Settings) -> Any:
    if settings.authority_store == "memory":
        return MemoryContextStore()
    return SqlContextStore(settings.audit_db_url)
