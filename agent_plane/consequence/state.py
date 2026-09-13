"""Task-scoped consequence state: what this task has already caused.

A single action's consequence, evaluated alone, misses composition: editing
a workflow definition is unremarkable, pushing to main is unremarkable, but
the second after the first makes a production deploy reachable in a way
neither step shows by itself. This is the accumulated record a task's
decisions build up as they go, so a later decision can ask "given what this
task has already done, what's reachable now" instead of only "what does
this one action do in isolation".

Two states per fact, not one, because a pre-tool hook only ever observes
*intent*: ``proposed`` is what a decision recorded before the tool ran (used
by Observe/Govern, and by Enforce's advisory reachability);  ``confirmed``
is what a completion signal later verified actually happened (see
``agent_plane.connect.hook``'s ``--post`` mode) - only confirmed facts gate
a binding Enforce-mode denial. Nothing here is inferred; every fact is
either what a connector said it was about to do or what it said it did.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import JSON, Integer, String, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from agent_plane.config import Settings
from agent_plane.storage import create_sql_engine

FactStatus = Literal["proposed", "confirmed"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TaskFact(BaseModel):
    """One thing this task has done, or is about to. ``kind`` is a
    ``ResourceProfile.semantic_class`` value - the small, closed vocabulary
    ``consequence.catalog`` classifies resources into, not free text."""

    kind: str
    resource: str
    created_by_decision: str
    status: FactStatus = "proposed"
    created_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, str] = Field(default_factory=dict)


class TaskConsequenceState(BaseModel):
    tenant: str
    task: str
    revision: int = 0
    facts: list[TaskFact] = Field(default_factory=list)

    def confirmed_kinds(self) -> frozenset[str]:
        """The fact kinds usable to gate a *binding* transition - see
        ``consequence.graph.Transition.requires_task_fact``."""
        return frozenset(f.kind for f in self.facts if f.status == "confirmed")

    def proposed_kinds(self) -> frozenset[str]:
        """Everything observed so far, proposed or confirmed - advisory
        reachability for Observe/Govern, or for an Enforce-mode connector
        that owns execution end to end and so never needs a confirm step."""
        return frozenset(f.kind for f in self.facts)


class Base(DeclarativeBase):
    pass


class TaskStateRow(Base):
    __tablename__ = "consequence_task_state"
    tenant: Mapped[str] = mapped_column(String(128), primary_key=True)
    task: Mapped[str] = mapped_column(String(200), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    document: Mapped[dict] = mapped_column(JSON)


class SqlTaskConsequenceStateStore:
    """CAS'd by an explicit ``revision`` column, not just a status guard -
    adding a fact means reading the current set first, then writing it back,
    so two sub-agents on the same task proposing facts at once must not
    silently drop one of them the way a plain ``UPDATE ... WHERE status=x``
    would (there's no single flag to guard on; the whole fact list changes).
    """

    def __init__(self, db_url: str):
        self._engine = create_sql_engine(db_url)
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine, class_=Session)

    def get(self, tenant: str, task: str) -> TaskConsequenceState:
        with self._session_factory() as session:
            row = session.get(TaskStateRow, (tenant, task))
            if row is None:
                return TaskConsequenceState(tenant=tenant, task=task)
            return TaskConsequenceState(
                tenant=tenant, task=task, revision=row.revision,
                facts=[TaskFact.model_validate(f) for f in (row.document or {}).get("facts", [])],
            )

    def propose(self, tenant: str, task: str, fact: TaskFact, *, max_retries: int = 5) -> TaskConsequenceState:
        """Add ``fact`` to the task's state, retrying the compare-and-swap
        against a concurrent writer rather than overwriting it."""
        for _ in range(max_retries):
            current = self.get(tenant, task)
            facts = [*current.facts, fact]
            if self._cas(tenant, task, current.revision, facts):
                return TaskConsequenceState(tenant=tenant, task=task, revision=current.revision + 1, facts=facts)
        raise RuntimeError(f"could not update consequence state for {tenant}/{task} - too much concurrent writing")

    def confirm(self, tenant: str, task: str, decision_id: str, *, max_retries: int = 5) -> int:
        """Flip every ``proposed`` fact this decision created to
        ``confirmed``. Returns how many changed (0 if the decision proposed
        nothing, or already confirmed it)."""
        for _ in range(max_retries):
            current = self.get(tenant, task)
            changed = 0
            facts: list[TaskFact] = []
            for f in current.facts:
                if f.created_by_decision == decision_id and f.status == "proposed":
                    f = f.model_copy(update={"status": "confirmed"})
                    changed += 1
                facts.append(f)
            if changed == 0:
                return 0
            if self._cas(tenant, task, current.revision, facts):
                return changed
        raise RuntimeError(f"could not confirm consequence state for {tenant}/{task} - too much concurrent writing")

    def _cas(self, tenant: str, task: str, expected_revision: int, facts: list[TaskFact]) -> bool:
        document = {"facts": [f.model_dump(mode="json") for f in facts]}
        with self._session_factory() as session:
            row = session.get(TaskStateRow, (tenant, task))
            if row is None:
                if expected_revision != 0:
                    return False  # someone else already created it since we read
                session.add(TaskStateRow(tenant=tenant, task=task, revision=1, document=document))
                try:
                    session.commit()
                    return True
                except Exception:  # noqa: BLE001 - concurrent insert of the same row
                    session.rollback()
                    return False
            result = session.execute(
                update(TaskStateRow).where(TaskStateRow.tenant == tenant, TaskStateRow.task == task,
                                           TaskStateRow.revision == expected_revision)
                .values(revision=expected_revision + 1, document=document)
            )
            session.commit()
            return result.rowcount == 1


def build_task_consequence_state(settings: Settings) -> SqlTaskConsequenceStateStore:
    return SqlTaskConsequenceStateStore(settings.audit_db_url)
