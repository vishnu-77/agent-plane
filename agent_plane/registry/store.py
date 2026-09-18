"""Registry records and stores (SQL by default, memory for tests).

Records are JSON documents keyed by ``tenant`` plus a stable id, so the
schema can grow without migrations. Everything is derived from governed
traffic and from lease issuance; nothing here is authoritative for a
decision - the evaluator only consults leases. The registry is the *view*
that explains a decision after the fact and drives Observe -> Enforce.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from agent_plane.config import Settings
from agent_plane.storage import create_sql_engine

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
    # An operator's hold on this one session, distinct from quarantine (which
    # holds the whole agent). Same absolute semantics: checked ahead of mode,
    # binds in observe/govern/enforce alike.
    paused: bool = False
    paused_by: str | None = None


def resolve_session_id(context: dict[str, str], *, agent: str, task: str | None) -> str:
    """The one place a session id is derived from a reported event's context,
    so a pause check (authority.service.decide) and the record it guards
    (_RegistryOps.observe) can never disagree about which session an action
    belongs to."""
    return context.get("session_id") or context.get("conversation_id") or f"{agent}:{task or 'no-task'}"


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


class CapabilityEvidence(BaseModel):
    """Why the registry believes a declared capability exists. See
    spec/principals.md and the identity-first roadmap's C/G/E/D model."""

    capability: str
    source: Literal["operator_declaration", "self_reported", "mcp_tool_discovery", "signed_identity_scope"]
    observed_at: datetime
    ref: str | None = None   # free-form provenance pointer, same convention as Origin.ref


class AgentRecord(BaseModel):
    id: str                          # agent id as carried by the identity (subject)
    tenant: str
    application: str = "default"
    framework: str | None = None
    # Which AgentDefinition (agent *type*, e.g. "claude-code") this instance
    # was observed running as. Never overwritten once set - see observe().
    definition_id: str | None = None
    status: AgentStatus = "active"
    first_seen: datetime
    last_seen: datetime
    parent_agent: str | None = None
    origin: Origin | None = None
    current_task: str | None = None
    tasks: list[str] = Field(default_factory=list)
    sessions: list[str] = Field(default_factory=list)
    declared_capabilities: list[str] = Field(default_factory=list)   # identity manifest
    capability_evidence: list[CapabilityEvidence] = Field(default_factory=list)
    requested_authority: dict[str, int] = Field(default_factory=dict) # action -> attempts
    exercised_authority: dict[str, int] = Field(default_factory=dict) # action -> allowed executions
    denied_authority: dict[str, int] = Field(default_factory=dict)    # action -> denials
    resources: dict[str, int] = Field(default_factory=dict)           # resource -> touches
    decisions: dict[str, int] = Field(default_factory=dict)           # outcome -> count
    last_action: dict[str, Any] | None = None
    quarantined_by: str | None = None
    quarantine_note: str | None = None


class AgentDefinition(BaseModel):
    """An agent *type* (e.g. "claude-code", "invoice-agent"), distinct from
    a running instance (AgentRecord). See spec/principals.md."""

    id: str
    tenant: str
    name: str
    framework: str | None = None
    owner: str | None = None
    expected_capabilities: list[str] = Field(default_factory=list)
    created_at: datetime


# --------------------------------------------------------------------------- #
# Store interface (memory)
# --------------------------------------------------------------------------- #
class MemoryRegistry:
    durable = False

    def __init__(self) -> None:
        self._agents: dict[tuple[str, str], AgentRecord] = {}
        self._definitions: dict[tuple[str, str], AgentDefinition] = {}
        self._tasks: dict[tuple[str, str], TaskRecord] = {}
        self._sessions: dict[tuple[str, str], SessionRecord] = {}
        self._settings: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    # -- raw access used by the shared logic below ------------------------------ #
    def _get_agent(self, tenant: str, agent: str) -> AgentRecord | None:
        return self._agents.get((tenant, agent))

    def _put_agent(self, rec: AgentRecord) -> None:
        self._agents[(rec.tenant, rec.id)] = rec

    def _get_definition(self, tenant: str, definition_id: str) -> AgentDefinition | None:
        return self._definitions.get((tenant, definition_id))

    def _put_definition(self, rec: AgentDefinition) -> None:
        self._definitions[(rec.tenant, rec.id)] = rec

    def _all_definitions(self, tenant: str | None) -> list[AgentDefinition]:
        return [d for (t, _), d in self._definitions.items() if tenant is None or t == tenant]

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
        for d in (self._agents, self._definitions, self._tasks, self._sessions):
            for key in [k for k in d if k[0] == tenant]:
                del d[key]

    def transaction(self):
        return self._lock

    @contextmanager
    def read_only(self):
        """No separate session to share in-process; matches SqlRegistry's interface."""
        yield self

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


class AgentDefinitionRow(Base):
    __tablename__ = "registry_agent_definitions"
    tenant: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
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
        self._engine = create_sql_engine(db_url)
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine, class_=Session)
        self._lock = threading.RLock()
        self._local = threading.local()

    @contextmanager
    def transaction(self):
        """One session, one commit, for everything this call does.

        Each ``_get_*``/``_put_*`` used to open and commit its own session, so
        a single ``register_task`` (get task, put task, get agent, put agent)
        paid four network round trips where one would do - the cost of every
        one of those is doubled on a database a region away, which is most of
        why reporting one action was slow. Calls made outside a ``with
        transaction():`` block (a plain read) are unaffected: they still open
        their own session, exactly as before.
        """
        depth = getattr(self._local, "depth", 0)
        self._local.depth = depth + 1
        try:
            if depth == 0:
                with self._lock, self._session_factory() as session:
                    self._local.session = session
                    try:
                        yield self
                        session.commit()
                    except BaseException:
                        session.rollback()
                        raise
                    finally:
                        self._local.session = None
            else:
                yield self  # nested call: ride the outer session and commit
        finally:
            self._local.depth = depth

    def _active_session(self) -> Session | None:
        return getattr(self._local, "session", None)

    @contextmanager
    def read_only(self):
        """Share one session for the reads in this block - no process lock,
        just one connection instead of one per read.

        decide() checks quarantine (one agent read) at the top and resolves
        the task record (one task read) at the bottom, with the whole
        authority evaluation - hundreds of milliseconds of unrelated work -
        in between. transaction()'s in-process lock would serialize every
        other registry access for that whole span if used here; this shares
        only the connection, not the lock.
        """
        if self._active_session() is not None:
            yield self  # already inside a transaction() or an outer read_only(): ride it
            return
        session = self._session_factory()
        try:
            self._local.session = session
            yield self
        finally:
            self._local.session = None
            session.commit()
            session.close()

    def _get_agent(self, tenant: str, agent: str) -> AgentRecord | None:
        session = self._active_session()
        if session is not None:
            row = session.get(AgentRow, (tenant, agent))
            return AgentRecord.model_validate(row.document) if row else None
        with self._session_factory() as s:
            row = s.get(AgentRow, (tenant, agent))
            return AgentRecord.model_validate(row.document) if row else None

    def _put_agent(self, rec: AgentRecord) -> None:
        session = self._active_session()
        if session is not None:
            row = session.get(AgentRow, (rec.tenant, rec.id))
            doc = rec.model_dump(mode="json")
            if row is None:
                session.add(AgentRow(tenant=rec.tenant, id=rec.id, last_seen=_naive(rec.last_seen), document=doc))
            else:
                row.last_seen, row.document = _naive(rec.last_seen), doc
            return
        with self._session_factory() as s:
            row = s.get(AgentRow, (rec.tenant, rec.id))
            doc = rec.model_dump(mode="json")
            if row is None:
                s.add(AgentRow(tenant=rec.tenant, id=rec.id, last_seen=_naive(rec.last_seen), document=doc))
            else:
                row.last_seen, row.document = _naive(rec.last_seen), doc
            s.commit()

    def _get_definition(self, tenant: str, definition_id: str) -> AgentDefinition | None:
        session = self._active_session()
        if session is not None:
            row = session.get(AgentDefinitionRow, (tenant, definition_id))
            return AgentDefinition.model_validate(row.document) if row else None
        with self._session_factory() as s:
            row = s.get(AgentDefinitionRow, (tenant, definition_id))
            return AgentDefinition.model_validate(row.document) if row else None

    def _put_definition(self, rec: AgentDefinition) -> None:
        session = self._active_session()
        if session is not None:
            row = session.get(AgentDefinitionRow, (rec.tenant, rec.id))
            doc = rec.model_dump(mode="json")
            if row is None:
                session.add(AgentDefinitionRow(tenant=rec.tenant, id=rec.id, document=doc))
            else:
                row.document = doc
            return
        with self._session_factory() as s:
            row = s.get(AgentDefinitionRow, (rec.tenant, rec.id))
            doc = rec.model_dump(mode="json")
            if row is None:
                s.add(AgentDefinitionRow(tenant=rec.tenant, id=rec.id, document=doc))
            else:
                row.document = doc
            s.commit()

    def _all_definitions(self, tenant: str | None) -> list[AgentDefinition]:
        with self._session_factory() as s:
            stmt = select(AgentDefinitionRow)
            if tenant:
                stmt = stmt.where(AgentDefinitionRow.tenant == tenant)
            return [AgentDefinition.model_validate(r.document) for r in s.scalars(stmt).all()]

    def _get_task(self, tenant: str, task: str) -> TaskRecord | None:
        session = self._active_session()
        if session is not None:
            row = session.get(TaskRow, (tenant, task))
            return TaskRecord.model_validate(row.document) if row else None
        with self._session_factory() as s:
            row = s.get(TaskRow, (tenant, task))
            return TaskRecord.model_validate(row.document) if row else None

    def _put_task(self, rec: TaskRecord) -> None:
        session = self._active_session()
        if session is not None:
            row = session.get(TaskRow, (rec.tenant, rec.id))
            doc = rec.model_dump(mode="json")
            if row is None:
                session.add(TaskRow(tenant=rec.tenant, id=rec.id, last_activity=_naive(rec.last_activity), document=doc))
            else:
                row.last_activity, row.document = _naive(rec.last_activity), doc
            return
        with self._session_factory() as s:
            row = s.get(TaskRow, (rec.tenant, rec.id))
            doc = rec.model_dump(mode="json")
            if row is None:
                s.add(TaskRow(tenant=rec.tenant, id=rec.id, last_activity=_naive(rec.last_activity), document=doc))
            else:
                row.last_activity, row.document = _naive(rec.last_activity), doc
            s.commit()

    def _get_session(self, tenant: str, sid: str) -> SessionRecord | None:
        session = self._active_session()
        if session is not None:
            row = session.get(SessionRow, (tenant, sid))
            return SessionRecord.model_validate(row.document) if row else None
        with self._session_factory() as s:
            row = s.get(SessionRow, (tenant, sid))
            return SessionRecord.model_validate(row.document) if row else None

    def _put_session(self, tenant: str, rec: SessionRecord) -> None:
        session = self._active_session()
        if session is not None:
            row = session.get(SessionRow, (tenant, rec.id))
            doc = rec.model_dump(mode="json")
            if row is None:
                session.add(SessionRow(tenant=tenant, id=rec.id, last_seen=_naive(rec.last_seen), document=doc))
            else:
                row.last_seen, row.document = _naive(rec.last_seen), doc
            return
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
            for model in (AgentRow, AgentDefinitionRow, TaskRow, SessionRow):
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
        executed: bool | None = None, evidence_source: str = "self_reported",
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
            if rec.definition_id is None and context.get("framework"):
                def_id = context["framework"]
                rec.definition_id = def_id
                if self._get_definition(tenant, def_id) is None:
                    self._put_definition(AgentDefinition(id=def_id, tenant=tenant, name=def_id,
                                                          framework=def_id, created_at=now))
            if declared:
                new_caps = set(declared) - set(rec.declared_capabilities)
                rec.declared_capabilities = sorted(set(rec.declared_capabilities) | set(declared))
                for cap in sorted(new_caps):
                    rec.capability_evidence.append(
                        CapabilityEvidence(capability=cap, source=evidence_source, observed_at=now))
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
            session_id = resolve_session_id(context, agent=agent, task=task)
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

    def definitions(self, tenant: str | None = None) -> list[AgentDefinition]:
        return self._all_definitions(tenant)

    def definition(self, tenant: str, definition_id: str) -> AgentDefinition | None:
        return self._get_definition(tenant, definition_id)

    def upsert_definition(self, rec: AgentDefinition) -> AgentDefinition:
        with self.transaction():
            self._put_definition(rec)
        return rec

    def tasks(self, tenant: str | None = None) -> list[TaskRecord]:
        return self._all_tasks(tenant)

    def task(self, tenant: str, task: str) -> TaskRecord | None:
        return self._get_task(tenant, task)

    def sessions(self, tenant: str | None = None) -> list[SessionRecord]:
        return self._all_sessions(tenant)

    def session(self, tenant: str, session_id: str) -> SessionRecord | None:
        return self._get_session(tenant, session_id)

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

    # -- session pause ------------------------------------------------------------ #
    def set_session_pause(self, tenant: str, session_id: str, *, on: bool,
                          by: str = "operator") -> SessionRecord | None:
        """Unlike quarantine, this never creates the session: pausing one you
        cannot already see (in the console, or via GET /v1/sessions) is not a
        thing an operator does."""
        with self.transaction():
            rec = self._get_session(tenant, session_id)
            if rec is None:
                return None
            rec.paused = on
            rec.paused_by = by if on else None
            self._put_session(tenant, rec)
            return rec

    def is_session_paused(self, tenant: str, session_id: str) -> bool:
        rec = self._get_session(tenant, session_id)
        return bool(rec and rec.paused)

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
            return {"agent": agent, "declared": [], "granted": granted_actions, "observed": [], "undeclared": [],
                    "ungranted": [], "unused_grants": [], "capability_outside_authority": [], "unused_authority": []}
        observed = sorted(rec.requested_authority)
        declared = rec.declared_capabilities
        def covered(action: str, grants: list[str]) -> bool:
            ns = action.split(".", 1)[0]
            return "*" in grants or action in grants or ns in grants
        # C/G/E/D model (roadmap phase 11): capability_outside_authority = C - G,
        # unused_authority = G - E. Exposure, not inherently a vulnerability.
        return {
            "agent": agent,
            "declared": declared,
            "granted": sorted(set(granted_actions)),
            "observed": observed,
            "undeclared": [a for a in observed if declared and not covered(a, declared)],
            "ungranted": [a for a in observed if a not in granted_actions],
            "unused_grants": [g for g in granted_actions if g not in observed],
            "capability_outside_authority": [c for c in declared if not covered(c, granted_actions)],
            "unused_authority": [g for g in granted_actions if g not in rec.exercised_authority],
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


for _name in ("observe", "register_task", "attach_lease", "agents", "agent", "definitions", "definition",
              "upsert_definition", "tasks", "task", "sessions",
              "session", "resources", "set_quarantine", "is_quarantined", "set_session_pause",
              "is_session_paused", "mode", "set_mode", "drift", "suggested_lease", "reset_tenant"):
    setattr(MemoryRegistry, _name, getattr(_RegistryOps, _name))
    setattr(SqlRegistry, _name, getattr(_RegistryOps, _name))


def build_registry(settings: Settings) -> Any:
    if settings.authority_store == "memory":
        return MemoryRegistry()
    return SqlRegistry(settings.audit_db_url)
