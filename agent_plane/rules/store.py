"""Authority rules: storage, matching, and compilation to AuthorityLease."""
from __future__ import annotations

import fnmatch
import hashlib
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import JSON, Boolean, DateTime, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from agent_plane.accounts.security import new_id
from agent_plane.authority.lease import AuthorityLease
from agent_plane.config import Settings


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _naive(dt: datetime | None) -> datetime | None:
    return dt.astimezone(UTC).replace(tzinfo=None) if dt is not None else None


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class RuleScope(BaseModel):
    """Who a rule applies to. Empty or ``["*"]`` means everyone."""

    agents: list[str] = Field(default_factory=lambda: ["*"])
    integrations: list[str] = Field(default_factory=lambda: ["*"])
    environments: list[str] = Field(default_factory=lambda: ["*"])

    @staticmethod
    def _matches(patterns: list[str], value: str | None) -> bool:
        if not patterns or "*" in patterns:
            return True
        if value is None:
            return False
        return any(fnmatch.fnmatchcase(value, p) for p in patterns)

    def covers_agent(self, agent: str | None) -> bool:
        """Agent dimension only: does this rule already speak for ``agent``?

        Used when drafting suggestions, so agent-plane stops proposing what a
        human has already decided.
        """
        return self._matches(self.agents, agent)

    def applies(self, *, agent: str | None, integration: str | None, environment: str | None) -> bool:
        return (self._matches(self.agents, agent)
                and self._matches(self.integrations, integration)
                and self._matches(self.environments, environment))

    def label(self) -> str:
        parts = []
        if self.agents and "*" not in self.agents:
            parts.append(", ".join(self.agents))
        if self.integrations and "*" not in self.integrations:
            parts.append(", ".join(self.integrations))
        if self.environments and "*" not in self.environments:
            parts.append(", ".join(self.environments))
        return " · ".join(parts) or "All agents"


class AuthorityRule(BaseModel):
    id: str
    project_id: str
    name: str
    scope: RuleScope = Field(default_factory=RuleScope)
    allow: list[str] = Field(default_factory=list)   # action patterns that may run
    ask: list[str] = Field(default_factory=list)     # in scope, but a human decides
    never: list[str] = Field(default_factory=list)   # refused, whatever else grants
    resources: list[str] = Field(default_factory=lambda: ["*"])
    protected_resources: list[str] = Field(default_factory=list)
    max_uses: dict[str, int] = Field(default_factory=dict)
    permitted_consequence: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    source: str = "manual"          # manual | template | suggested
    created_at: datetime
    updated_at: datetime
    created_by: str = ""
    order: int = 100                # lower runs first; only affects display

    def summary(self) -> str:
        bits = []
        if self.allow:
            bits.append(f"{len(self.allow)} allowed")
        if self.ask:
            bits.append(f"{len(self.ask)} ask first")
        if self.never:
            bits.append(f"{len(self.never)} never")
        return " · ".join(bits) or "no actions"


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
class Base(DeclarativeBase):
    pass


class RuleRow(Base):
    __tablename__ = "authority_rules"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    order: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    document: Mapped[dict] = mapped_column(JSON)


class RuleStore:
    def __init__(self, db_url: str):
        connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        self._engine = create_engine(db_url, connect_args=connect_args, future=True)
        Base.metadata.create_all(self._engine)
        self._sessions = sessionmaker(bind=self._engine, class_=Session)
        self._lock = threading.RLock()

    @staticmethod
    def _rule(row: RuleRow) -> AuthorityRule:
        return AuthorityRule.model_validate(row.document)

    def create(self, project_id: str, payload: dict[str, Any], *, created_by: str = "") -> AuthorityRule:
        now = _utcnow()
        rule = AuthorityRule(
            id=payload.get("id") or new_id("rule"), project_id=project_id,
            name=str(payload.get("name") or "Rule").strip()[:120],
            scope=RuleScope.model_validate(payload.get("scope") or {}),
            allow=list(payload.get("allow") or []), ask=list(payload.get("ask") or []),
            never=list(payload.get("never") or []),
            resources=list(payload.get("resources") or ["*"]),
            protected_resources=list(payload.get("protected_resources") or []),
            max_uses=dict(payload.get("max_uses") or {}),
            permitted_consequence=dict(payload.get("permitted_consequence") or {}),
            enabled=bool(payload.get("enabled", True)),
            source=str(payload.get("source") or "manual"),
            created_at=now, updated_at=now, created_by=created_by,
            order=int(payload.get("order") or 100),
        )
        with self._lock, self._sessions() as s:
            s.add(RuleRow(id=rule.id, project_id=project_id, enabled=rule.enabled, order=rule.order,
                          created_at=_naive(now), updated_at=_naive(now),
                          document=rule.model_dump(mode="json")))
            s.commit()
        return rule

    def update(self, rule_id: str, payload: dict[str, Any]) -> AuthorityRule | None:
        with self._lock, self._sessions() as s:
            row = s.get(RuleRow, rule_id)
            if row is None:
                return None
            current = self._rule(row).model_dump(mode="json")
            for field in ("name", "allow", "ask", "never", "resources", "protected_resources",
                          "max_uses", "permitted_consequence", "enabled", "order", "scope"):
                if field in payload:
                    current[field] = payload[field]
            current["updated_at"] = _utcnow().isoformat()
            rule = AuthorityRule.model_validate(current)
            row.document = rule.model_dump(mode="json")
            row.enabled = rule.enabled
            row.order = rule.order
            row.updated_at = _naive(rule.updated_at)
            s.commit()
            return rule

    def delete(self, rule_id: str) -> bool:
        with self._lock, self._sessions() as s:
            row = s.get(RuleRow, rule_id)
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True

    def get(self, rule_id: str) -> AuthorityRule | None:
        with self._sessions() as s:
            row = s.get(RuleRow, rule_id)
            return self._rule(row) if row else None

    def list(self, project_id: str, *, enabled_only: bool = False) -> list[AuthorityRule]:
        with self._sessions() as s:
            stmt = select(RuleRow).where(RuleRow.project_id == project_id).order_by(RuleRow.order, RuleRow.created_at)
            if enabled_only:
                stmt = stmt.where(RuleRow.enabled.is_(True))
            return [self._rule(r) for r in s.scalars(stmt).all()]


def build_rule_store(settings: Settings) -> RuleStore:
    return RuleStore(settings.audit_db_url)


# --------------------------------------------------------------------------- #
# Compilation: rules -> AuthorityLease
# --------------------------------------------------------------------------- #
COMPILED_LEASE_PREFIX = "rules:"


def compiled_lease_id(project_id: str, agent: str, task: str) -> str:
    """The deterministic id of the lease a project's rules compile to."""
    return f"{COMPILED_LEASE_PREFIX}{project_id}:{agent}:{task}"


def _fingerprint(rules: list[AuthorityRule]) -> str:
    """Changes whenever an applicable rule changes, so the compiled lease is rebuilt."""
    raw = "|".join(f"{r.id}@{r.updated_at.isoformat()}" for r in sorted(rules, key=lambda r: r.id))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def compile_rules(
    rules: list[AuthorityRule], *, project_id: str, agent: str, task: str,
    integration: str | None = None, environment: str | None = None,
    ttl_seconds: int = 3600,
) -> AuthorityLease | None:
    """Fold every applicable rule into one ephemeral lease.

    Union of ``allow`` and ``ask`` becomes the granted actions; ``ask`` also
    becomes ``require_approval``; ``never`` becomes ``denied_actions``, which
    the evaluator treats as an absolute refusal. Returns None when no rule
    applies, which leaves the project default-deny.
    """
    applicable = [r for r in rules if r.enabled
                  and r.scope.applies(agent=agent, integration=integration, environment=environment)]
    if not applicable:
        return None
    allow: list[str] = []
    ask: list[str] = []
    never: list[str] = []
    resources: list[str] = []
    protected: list[str] = []
    max_uses: dict[str, int] = {}
    consequence: dict[str, Any] = {}
    for rule in applicable:
        for src, dst in ((rule.allow, allow), (rule.ask, ask), (rule.never, never),
                         (rule.resources, resources), (rule.protected_resources, protected)):
            for item in src:
                if item not in dst:
                    dst.append(item)
        for action, limit in rule.max_uses.items():
            max_uses[action] = min(limit, max_uses.get(action, limit))
        for key, value in rule.permitted_consequence.items():
            # The narrowest bound across applicable rules wins.
            if key not in consequence:
                consequence[key] = value
            elif isinstance(value, list) and isinstance(consequence[key], list):
                consequence[key] = [v for v in consequence[key] if v in value]
            elif isinstance(value, bool):
                consequence[key] = consequence[key] and value
            elif isinstance(value, int) and not isinstance(value, bool):
                consequence[key] = min(consequence[key], value)
    return AuthorityLease(
        id=compiled_lease_id(project_id, agent, task),
        task=task, subject=agent, tenant=project_id,
        resources=resources or ["*"],
        actions=sorted({*allow, *ask}),
        denied_actions=sorted(set(never)),
        protected_resources=sorted(set(protected)),
        require_approval=sorted(set(ask)),
        max_uses=max_uses,
        permitted_consequence=consequence,
        expires_at=_utcnow() + timedelta(seconds=ttl_seconds),
        child_authority="subset_only",
        origin={"kind": "rules", "created_by": "project rules",
                "rules": [r.id for r in applicable],
                "fingerprint": _fingerprint(applicable)},
    )


def suggest_rule(*, project_id: str, observed: dict[str, int], denied: dict[str, int],
                 resources: list[str], agent: str | None = None) -> dict[str, Any]:
    """Turn observed behaviour into a reviewable rule draft.

    Read-shaped actions are proposed as ALLOW, writes and deletes as ASK, and
    anything already refused stays out of the draft entirely. A human reviews
    it before it becomes authority.
    """
    def bucket(action: str) -> str:
        verb = action.rsplit(".", 1)[-1].lower()
        if any(verb.startswith(v) for v in ("read", "list", "get", "search", "describe", "view")):
            return "allow"
        if any(verb.startswith(v) for v in ("delete", "destroy", "remove", "drop", "purge")):
            return "never"
        return "ask"

    allow, ask, never = [], [], []
    for action in sorted(observed):
        {"allow": allow, "ask": ask, "never": never}[bucket(action)].append(action)
    for action in sorted(denied):
        if action not in never and action not in ask and action not in allow:
            ask.append(action)
    return {
        "name": f"{agent or 'agents'} — suggested from observed activity",
        "project_id": project_id,
        "scope": {"agents": [agent] if agent else ["*"]},
        "allow": allow, "ask": ask, "never": never,
        "resources": sorted({r.rsplit("/", 1)[0] + "/*" if "/" in r else r for r in resources}) or ["*"],
        "source": "suggested",
        "basis": {"observed": observed, "denied": denied},
    }
