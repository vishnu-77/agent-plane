"""Registry records and stores (SQL by default, memory for tests).

Records are JSON documents keyed by ``tenant`` plus a stable id, so the
schema can grow without migrations. Everything is derived from governed
traffic and from lease issuance; nothing here is authoritative for a
decision - the evaluator only consults leases. The registry is the *view*
that explains a decision after the fact and drives Observe -> Enforce.
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from agent_plane.config import Settings

AgentStatus = Literal["active", "idle", "quarantined"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #
class Origin(BaseModel):
    """Where a task came from. Prompts are provenance, never permission."""

    kind: str = "unknown"          # human | prompt | event | schedule | parent | api
    ref: str | None = None         # prompt id, event id, ticket, user id ...
    text: str | None = None        # the instruction, when the creator chose to record it
    created_by: str | None = None  # human or service identity that raised the intent
    parent_task: str | None = None
    parent_agent: str | None = None


class SessionRecord(BaseModel):
    id: str
    agent: str
    task: str | None = None
    started_at: datetime
    last_seen: datetime
    actions: int = 0


class TaskRecord(BaseModel):
    id: str
    tenant: str
    origin: Origin = Field(default_factory=Origin)
    agents: list[str] = Field(default_factory=list)
    leases: list[str] = Field(default_factory=list)
    status: str = "active"          # active | completed | abandoned
    created_at: datetime
    last_activity: datetime
    decisions: dict[str, int] = Field(default_factory=dict)   # outcome -> count
    observed_actions: dict[str, int] = Field(default_factory=dict)
    resources: list[str] = Field(default_factory=list)


class AgentRecord(BaseModel):
    id: str                          # agent id as carried by the identity (subject)
    tenant: str
    application: str = "default"
    framework: str | None = None
    status: AgentStatus = "active"
    first_seen: datetime
    last_seen: datetime
    parent_agent: str | None = None
    origin: Origin | None = None
    current_task: str | None = None
    tasks: list[str] = Field(default_factory=list)
    sessions: list[str] = Field(default_factory=list)
    declared_capabilities: list[str] = Field(default_factory=list)   # identity manifest
    requested_authority: dict[str, int] = Field(default_factory=dict) # action -> attempts
    exercised_authority: dict[str, int] = Field(default_factory=dict) # action -> allowed executions
    denied_authority: dict[str, int] = Field(default_factory=dict)    # action -> denials
    resources: dict[str, int] = Field(default_factory=dict)           # resource -> touches
    decisions: dict[str, int] = Field(default_factory=dict)           # outcome -> count
    last_action: dict[str, Any] | None = None
    quarantined_by: str | None = None
    quarantine_note: str | None = None


# --------------------------------------------------------------------------- #
# Store interface (memory)
# --------------------------------------------------------------------------- #
class MemoryRegistry:
    durable = False

    def __init__(self) -> None:
        self._agents: dict[tuple[str, str], AgentRecord] = {}
        self._tasks: dict[tuple[str, str], TaskRecord] = {}
        self._sessions: dict[tuple[str, str], SessionRecord] = {}
        self._settings: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    # -- raw access used by the shared logic below ------------------------------ #
    def _get_agent(self, tenant: str, agent: str) -> AgentRecord | None:
        return self._agents.get((tenant, agent))

    def _put_agent(self, rec: AgentRecord) -> None:
        self._agents[(rec.tenant, rec.id)] = rec

    def _get_task(self, tenant: str, task: str) -> TaskRecord | None:
        return self._tasks.get((tenant, task))

    def _put_task(self, rec: TaskRecord) -> None:
        self._tasks[(rec.tenant, rec.id)] = rec

    def _get_session(self, tenant: str, sid: str) -> SessionRecord | None:
        return self._sessions.get((tenant, sid))

    def _put_session(self, tenant: str, rec: SessionRecord) -> None:
        self._sessions[(tenant, rec.id)] = rec

    def _all_agents(self, tenant: str | None) -> list[AgentRecord]:
        return [a for (t, _), a in self._agents.items() if tenant is None or t == tenant]

    def _all_tasks(self, tenant: str | None) -> list[TaskRecord]:
        return [t for (tn, _), t in self._tasks.items() if tenant is None or tn == tenant]

    def _all_sessions(self, tenant: str | None) -> list[SessionRecord]:
        return [s for (tn, _), s in self._sessions.items() if tenant is None or tn == tenant]

    def _get_setting(self, key: str) -> dict[str, Any] | None:
        return self._settings.get(key)

    def _put_setting(self, key: str, value: dict[str, Any]) -> None:
        self._settings[key] = value

    def _delete_tenant(self, tenant: str) -> None:
        for d in (self._agents, self._tasks, self._sessions):
            for key in [k for k in d if k[0] == tenant]:
                del d[key]

    def transaction(self):
        return self._lock

    # shared behaviour
    observe = None  # assigned below
    register_task = None
    attach_lease = None


# --------------------------------------------------------------------------- #
# SQL store
# --------------------------------------------------------------------------- #
class Base(DeclarativeBase):
    pass


class AgentRow(Base):
    __tablename__ = "registry_agents"
    tenant: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime, index=True)
    document: Mapped[dict] = mapped_column(JSON)


class TaskRow(Base):
    __tablename__ = "registry_tasks"
    tenant: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    last_activity: Mapped[datetime] = mapped_column(DateTime, index=True)
    document: Mapped[dict] = mapped_column(JSON)


class SessionRow(Base):
    __tablename__ = "registry_sessions"
    tenant: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime, index=True)
    document: Mapped[dict] = mapped_column(JSON)


class SettingRow(Base):
    __tablename__ = "registry_settings"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    document: Mapped[dict] = mapped_column(JSON)


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


class SqlRegistry:
    durable = True

    def __init__(self, db_url: str):
        connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        self._engine = create_engine(db_url, connect_args=connect_args, future=True)
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine, class_=Session)
        self._lock = threading.RLock()

    def transaction(self):
        return self._lock

    def _get_agent(self, tenant: str, agent: str) -> AgentRecord | None:
        with self._session_factory() as s:
            row = s.get(AgentRow, (tenant, agent))
            return AgentRecord.model_validate(row.document) if row else None

    def _put_agent(self, rec: AgentRecord) -> None:
        with self._session_factory() as s:
            row = s.get(AgentRow, (rec.tenant, rec.id))
            doc = rec.model_dump(mode="json")
            if row is None:
                s.add(AgentRow(tenant=rec.tenant, id=rec.id, last_seen=_naive(rec.last_seen), document=doc))
            else:
                row.last_seen, row.document = _naive(rec.last_seen), doc
            s.commit()

    def _get_task(self, tenant: str, task: str) -> TaskRecord | None:
        with self._session_factory() as s:
            row = s.get(TaskRow, (tenant, task))
            return TaskRecord.model_validate(row.document) if row else None

    def _put_task(self, rec: TaskRecord) -> None:
        with self._session_factory() as s:
            row = s.get(TaskRow, (rec.tenant, rec.id))
            doc = rec.model_dump(mode="json")
            if row is None:
                s.add(TaskRow(tenant=rec.tenant, id=rec.id, last_activity=_naive(rec.last_activity), document=doc))
            else:
                row.last_activity, row.document = _naive(rec.last_activity), doc
            s.commit()

    def _get_session(self, tenant: str, sid: str) -> SessionRecord | None:
        with self._session_factory() as s:
            row = s.get(SessionRow, (tenant, sid))
            return SessionRecord.model_validate(row.document) if row else None

    def _put_session(self, tenant: str, rec: SessionRecord) -> None:
        with self._session_factory() as s:
            row = s.get(SessionRow, (tenant, rec.id))
            doc = rec.model_dump(mode="json")
            if row is None:
                s.add(SessionRow(tenant=tenant, id=rec.id, last_seen=_naive(rec.last_seen), document=doc))
            else:
                row.last_seen, row.document = _naive(rec.last_seen), doc
            s.commit()

    def _all_agents(self, tenant: str | None) -> list[AgentRecord]:
        with self._session_factory() as s:
            stmt = select(AgentRow).order_by(AgentRow.last_seen.desc())
            if tenant:
                stmt = stmt.where(AgentRow.tenant == tenant)
            return [AgentRecord.model_validate(r.document) for r in s.scalars(stmt).all()]

    def _all_tasks(self, tenant: str | None) -> list[TaskRecord]:
        with self._session_factory() as s:
            stmt = select(TaskRow).order_by(TaskRow.last_activity.desc())
            if tenant:
                stmt = stmt.where(TaskRow.tenant == tenant)
            return [TaskRecord.model_validate(r.document) for r in s.scalars(stmt).all()]

    def _all_sessions(self, tenant: str | None) -> list[SessionRecord]:
        with self._session_factory() as s:
            stmt = select(SessionRow).order_by(SessionRow.last_seen.desc())
            if tenant:
                stmt = stmt.where(SessionRow.tenant == tenant)
            return [SessionRecord.model_validate(r.document) for r in s.scalars(stmt).all()]

    def _get_setting(self, key: str) -> dict[str, Any] | None:
        with self._session_factory() as s:
            row = s.get(SettingRow, key)
            return dict(row.document) if row else None

    def _put_setting(self, key: str, value: dict[str, Any]) -> None:
        with self._session_factory() as s:
            row = s.get(SettingRow, key)
            if row is None:
                s.add(SettingRow(key=key, document=value))
            else:
                row.document = value
            s.commit()

    def _delete_tenant(self, tenant: str) -> None:
        with self._session_factory() as s:
            for model in (AgentRow, TaskRow, SessionRow):
                for row in s.scalars(select(model).where(model.tenant == tenant)).all():
                    s.delete(row)
            s.commit()


# --------------------------------------------------------------------------- #
# Shared behaviour (mixed into both stores)
# --------------------------------------------------------------------------- #
class _RegistryOps:
    """Operations expressed over the primitive get/put methods above."""

    # -- discovery ------------------------------------------------------------ #
    def observe(
        self, *, tenant: str, agent: str, application: str | None, declared: list[str],
        task: str | None, action: str, resource: str, outcome: str, decision_id: str,
        context: dict[str, str] | None = None, edge: str = "authorize",
        executed: bool | None = None,
    ) -> AgentRecord:
        """Record one governed call. Idempotent per decision id is not required;
        counts are approximate telemetry, decisions are the audit chain."""
        context = dict(context or {})
        now = _utcnow()
        with self.transaction():
            rec = self._get_agent(tenant, agent)
            if rec is None:
                rec = AgentRecord(id=agent, tenant=tenant, application=application or "default",
                                  first_seen=now, last_seen=now)
            rec.last_seen = now
            if application and rec.application == "default":
                rec.application = application
            if context.get("framework") and not rec.framework:
                rec.framework = context["framework"]
            if context.get("parent_agent") and not rec.parent_agent:
                rec.parent_agent = context["parent_agent"]
            if declared:
                rec.declared_capabilities = sorted(set(rec.declared_capabilities) | set(declared))
            if task:
                rec.current_task = task
                if task not in rec.tasks:
                    rec.tasks.append(task)
            if rec.status == "idle":
                rec.status = "active"
            rec.requested_authority[action] = rec.requested_authority.get(action, 0) + 1
            if outcome == "allow" and executed is not False:
                rec.exercised_authority[action] = rec.exercised_authority.get(action, 0) + 1
            elif outcome in ("deny", "quarantine"):
                rec.denied_authority[action] = rec.denied_authority.get(action, 0) + 1
            rec.resources[resource] = rec.resources.get(resource, 0) + 1
            rec.decisions[outcome] = rec.decisions.get(outcome, 0) + 1
            rec.last_action = {"action": action, "resource": resource, "outcome": outcome,
                               "decision_id": decision_id, "at": now.isoformat(), "edge": edge}
            session_id = context.get("session_id") or context.get("conversation_id") or f"{agent}:{task or 'no-task'}"
            if session_id not in rec.sessions:
                rec.sessions.append(session_id)
            self._put_agent(rec)

            sess = self._get_session(tenant, session_id)
            if sess is None:
                sess = SessionRecord(id=session_id, agent=agent, task=task, started_at=now, last_seen=now)
            sess.last_seen, sess.actions = now, sess.actions + 1
            if task:
                sess.task = task
            self._put_session(tenant, sess)

            if task:
                trec = self._get_task(tenant, task)
                if trec is None:
                    origin = Origin(kind="prompt" if context.get("prompt_hash") else "unknown",
                                    ref=context.get("prompt_hash") or context.get("request_id"),
                                    parent_agent=context.get("parent_agent"),
                                    created_by=context.get("origin"))
                    trec = TaskRecord(id=task, tenant=tenant, origin=origin, created_at=now, last_activity=now)
                trec.last_activity = now
                if agent not in trec.agents:
                    trec.agents.append(agent)
                trec.decisions[outcome] = trec.decisions.get(outcome, 0) + 1
                trec.observed_actions[action] = trec.observed_actions.get(action, 0) + 1
                if resource not in trec.resources:
                    trec.resources.append(resource)
                self._put_task(trec)
        return rec

    def register_task(self, *, tenant: str, task: str, origin: Origin, agent: str | None = None) -> TaskRecord:
        """Explicit intent registration (human, service, event, or parent agent)."""
        now = _utcnow()
        with self.transaction():
            rec = self._get_task(tenant, task)
            if rec is None:
                rec = TaskRecord(id=task, tenant=tenant, origin=origin, created_at=now, last_activity=now)
            else:
                # Provenance accumulates. A later report that carries no origin
                # (an agent reporting a tool call, say) must not erase the prompt
                # or the human we already recorded for this task.
                merged = rec.origin.model_dump()
                for field, value in origin.model_dump().items():
                    if value and not (field == "kind" and value == "unknown"):
                        merged[field] = value
                rec.origin = Origin.model_validate(merged)
                rec.last_activity = now
            if agent and agent not in rec.agents:
                rec.agents.append(agent)
            self._put_task(rec)
            if agent:
                arec = self._get_agent(tenant, agent)
                if arec is None:
                    arec = AgentRecord(id=agent, tenant=tenant, first_seen=now, last_seen=now)
                arec.current_task = task
                if task not in arec.tasks:
                    arec.tasks.append(task)
                if origin.parent_agent and not arec.parent_agent:
                    arec.parent_agent = origin.parent_agent
                if arec.origin is None:
                    arec.origin = origin
                self._put_agent(arec)
        return rec

    def attach_lease(self, *, tenant: str, task: str, agent: str, lease_id: str,
                     parent_agent: str | None = None) -> None:
        now = _utcnow()
        with self.transaction():
            trec = self._get_task(tenant, task)
            if trec is None:
                trec = TaskRecord(id=task, tenant=tenant, created_at=now, last_activity=now)
            if lease_id not in trec.leases:
                trec.leases.append(lease_id)
            if agent not in trec.agents:
                trec.agents.append(agent)
            trec.last_activity = now
            self._put_task(trec)
            arec = self._get_agent(tenant, agent)
            if arec is None:
                arec = AgentRecord(id=agent, tenant=tenant, first_seen=now, last_seen=now)
            if task not in arec.tasks:
                arec.tasks.append(task)
            arec.current_task = arec.current_task or task
            if parent_agent and not arec.parent_agent:
                arec.parent_agent = parent_agent
            self._put_agent(arec)

    # -- reads ------------------------------------------------------------------ #
    def agents(self, tenant: str | None = None) -> list[AgentRecord]:
        return self._all_agents(tenant)

    def agent(self, tenant: str, agent: str) -> AgentRecord | None:
        return self._get_agent(tenant, agent)

    def tasks(self, tenant: str | None = None) -> list[TaskRecord]:
        return self._all_tasks(tenant)

    def task(self, tenant: str, task: str) -> TaskRecord | None:
        return self._get_task(tenant, task)

    def sessions(self, tenant: str | None = None) -> list[SessionRecord]:
        return self._all_sessions(tenant)

    def resources(self, tenant: str | None = None) -> list[dict[str, Any]]:
        touched: dict[str, dict[str, Any]] = {}
        for a in self._all_agents(tenant):
            for res, n in a.resources.items():
                entry = touched.setdefault(res, {"resource": res, "touches": 0, "agents": [], "tenant": a.tenant})
                entry["touches"] += n
                if a.id not in entry["agents"]:
                    entry["agents"].append(a.id)
        return sorted(touched.values(), key=lambda e: -e["touches"])

    # -- quarantine ---------------------------------------------------------------- #
    def set_quarantine(self, tenant: str, agent: str, *, on: bool, by: str = "admin",
                       note: str | None = None) -> AgentRecord | None:
        now = _utcnow()
        with self.transaction():
            rec = self._get_agent(tenant, agent)
            if rec is None:
                if not on:
                    return None
                rec = AgentRecord(id=agent, tenant=tenant, first_seen=now, last_seen=now)
            rec.status = "quarantined" if on else "active"
            rec.quarantined_by = by if on else None
            rec.quarantine_note = note if on else None
            self._put_agent(rec)
            return rec

    def is_quarantined(self, tenant: str, agent: str) -> bool:
        rec = self._get_agent(tenant, agent)
        return bool(rec and rec.status == "quarantined")

    # -- mode (observe / enforce) ---------------------------------------------------- #
    def mode(self, tenant: str, default: str) -> str:
        doc = self._get_setting(f"mode:{tenant}") or self._get_setting("mode:*")
        return str(doc["mode"]) if doc and doc.get("mode") in ("observe", "enforce") else default

    def set_mode(self, tenant: str | None, mode: str) -> None:
        self._put_setting(f"mode:{tenant or '*'}", {"mode": mode})

    # -- drift ------------------------------------------------------------------------- #
    def drift(self, tenant: str, agent: str, granted_actions: list[str]) -> dict[str, Any]:
        rec = self._get_agent(tenant, agent)
        if rec is None:
            return {"agent": agent, "declared": [], "granted": granted_actions, "observed": [], "undeclared": [], "ungranted": []}
        observed = sorted(rec.requested_authority)
        declared = rec.declared_capabilities
        def covered(action: str, grants: list[str]) -> bool:
            ns = action.split(".", 1)[0]
            return "*" in grants or action in grants or ns in grants
        return {
            "agent": agent,
            "declared": declared,
            "granted": sorted(set(granted_actions)),
            "observed": observed,
            "undeclared": [a for a in observed if declared and not covered(a, declared)],
            "ungranted": [a for a in observed if a not in granted_actions],
            "unused_grants": [g for g in granted_actions if g not in observed],
        }

    def suggested_lease(self, tenant: str, agent: str, task: str | None = None) -> dict[str, Any]:
        """A lease shape inferred from what the agent actually did (Observe -> Enforce)."""
        rec = self._get_agent(tenant, agent)
        if rec is None:
            return {}
        actions = sorted(rec.requested_authority)
        resources = sorted(rec.resources)
        return {
            "id": f"lease-{agent}-{(task or rec.current_task or 'task')}"[:120].replace(":", "-"),
            "subject": agent, "tenant": tenant, "task": task or rec.current_task or "",
            "actions": actions, "resources": resources,
            "protected_resources": [], "require_approval": [], "max_uses": {},
            "maximum_impact": "reversible", "child_authority": "none",
            "basis": {"observed_actions": rec.requested_authority, "observed_resources": rec.resources},
        }

    def reset_tenant(self, tenant: str) -> None:
        with self.transaction():
            self._delete_tenant(tenant)


for _name in ("observe", "register_task", "attach_lease", "agents", "agent", "tasks", "task", "sessions",
              "resources", "set_quarantine", "is_quarantined", "mode", "set_mode", "drift",
              "suggested_lease", "reset_tenant"):
    setattr(MemoryRegistry, _name, getattr(_RegistryOps, _name))
    setattr(SqlRegistry, _name, getattr(_RegistryOps, _name))


def build_registry(settings: Settings) -> Any:
    if settings.authority_store == "memory":
        return MemoryRegistry()
    return SqlRegistry(settings.audit_db_url)
