"""Account storage: users, workspaces, projects, keys, integrations.

Shares the audit database (SQLite by default, Postgres in production) so a
deployment has one durable store, not four. Keys are looked up by their
indexed HMAC, so authenticating a request is a single indexed read.
"""
from __future__ import annotations

import re
import threading
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from agent_plane.accounts.models import (
    DEFAULT_COLLECTION,
    ApiKey,
    ApiKeyRow,
    Base,
    Integration,
    IntegrationRow,
    Membership,
    MembershipRow,
    Project,
    ProjectRow,
    Role,
    User,
    UserRow,
    Workspace,
    WorkspaceRow,
)
from agent_plane.accounts.security import (
    generate_api_key,
    hash_api_key,
    hash_password,
    new_id,
    verify_password,
)
from agent_plane.config import Settings

DEMO_PROJECT_ID = "prj_demo"
DEMO_WORKSPACE_ID = "wsp_demo"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _naive(dt: datetime | None) -> datetime | None:
    return dt.astimezone(UTC).replace(tzinfo=None) if dt is not None else None


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def slugify(value: str, fallback: str = "project") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return (slug or fallback)[:60]


class AccountError(Exception):
    """A rejected account operation (duplicate email, unknown project, ...)."""


class AccountStore:
    def __init__(self, db_url: str, key_secret: str):
        connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        self._engine = create_engine(db_url, connect_args=connect_args, future=True)
        Base.metadata.create_all(self._engine)
        self._sessions = sessionmaker(bind=self._engine, class_=Session)
        self._key_secret = key_secret
        self._lock = threading.RLock()

    # -- conversion ----------------------------------------------------------- #
    @staticmethod
    def _user(row: UserRow) -> User:
        return User(id=row.id, email=row.email, name=row.name, created_at=_aware(row.created_at),
                    last_login_at=_aware(row.last_login_at))

    @staticmethod
    def _workspace(row: WorkspaceRow) -> Workspace:
        return Workspace(id=row.id, name=row.name, slug=row.slug, created_at=_aware(row.created_at),
                         created_by=row.created_by)

    @staticmethod
    def _project(row: ProjectRow) -> Project:
        return Project(id=row.id, workspace_id=row.workspace_id, name=row.name, slug=row.slug,
                       mode=row.mode, collection={**DEFAULT_COLLECTION, **(row.collection or {})},
                       created_at=_aware(row.created_at), created_by=row.created_by, demo=bool(row.demo))

    @staticmethod
    def _key(row: ApiKeyRow) -> ApiKey:
        return ApiKey(id=row.id, project_id=row.project_id, name=row.name, prefix=row.prefix,
                      last4=row.last4, environment=row.environment, scopes=list(row.scopes or []),
                      created_at=_aware(row.created_at), created_by=row.created_by,
                      last_used_at=_aware(row.last_used_at), expires_at=_aware(row.expires_at),
                      revoked_at=_aware(row.revoked_at))

    @staticmethod
    def _integration(row: IntegrationRow) -> Integration:
        return Integration(id=row.id, project_id=row.project_id, kind=row.kind, name=row.name,
                           host=row.host, status=row.status, config=dict(row.config or {}),
                           agents=list(row.agents or []), actions=int(row.actions or 0),
                           created_at=_aware(row.created_at), last_seen_at=_aware(row.last_seen_at))

    # -- users ---------------------------------------------------------------- #
    def user_count(self) -> int:
        with self._sessions() as s:
            return int(s.scalar(select(func.count()).select_from(UserRow)) or 0)

    def is_instance_owner(self, user_id: str) -> bool:
        """Is this the account that set the deployment up?

        Deployment-wide operations (reload policy, revoke credentials, issue or
        revoke a lease, quarantine an agent) are not project operations, and on
        a shared instance every account must not be able to perform them. The
        first account created owns the instance; everyone else uses their own
        projects, a management key, or the deployment's admin token.
        """
        with self._sessions() as s:
            first = s.scalar(select(UserRow.id).order_by(UserRow.created_at, UserRow.id).limit(1))
        return bool(first) and first == user_id

    def create_user(self, *, email: str, password: str, name: str = "") -> User:
        email = email.strip().lower()
        with self._lock, self._sessions() as s:
            if s.scalar(select(UserRow).where(UserRow.email == email)):
                raise AccountError("An account with that email already exists")
            row = UserRow(id=new_id("usr"), email=email, name=name.strip() or email.split("@")[0],
                          password_hash=hash_password(password), created_at=_naive(_utcnow()))
            s.add(row)
            s.commit()
            return self._user(row)

    def authenticate(self, email: str, password: str) -> User | None:
        with self._sessions() as s:
            row = s.scalar(select(UserRow).where(UserRow.email == email.strip().lower()))
            if row is None or not verify_password(password, row.password_hash):
                return None
            row.last_login_at = _naive(_utcnow())
            s.commit()
            return self._user(row)

    def user(self, user_id: str) -> User | None:
        with self._sessions() as s:
            row = s.get(UserRow, user_id)
            return self._user(row) if row else None

    # -- workspaces and membership ---------------------------------------------- #
    def create_workspace(self, *, name: str, owner: str) -> Workspace:
        with self._lock, self._sessions() as s:
            slug = slugify(name, "workspace")
            if s.scalar(select(WorkspaceRow).where(WorkspaceRow.slug == slug)):
                slug = f"{slug}-{new_id('w', 6).split('_')[1]}"
            row = WorkspaceRow(id=new_id("wsp"), name=name.strip() or "Workspace", slug=slug,
                               created_at=_naive(_utcnow()), created_by=owner)
            s.add(row)
            s.add(MembershipRow(workspace_id=row.id, user_id=owner, role="owner", created_at=_naive(_utcnow())))
            s.commit()
            return self._workspace(row)

    def workspaces_for(self, user_id: str) -> list[Workspace]:
        with self._sessions() as s:
            ids = [m.workspace_id for m in s.scalars(select(MembershipRow).where(MembershipRow.user_id == user_id)).all()]
            if not ids:
                return []
            rows = s.scalars(select(WorkspaceRow).where(WorkspaceRow.id.in_(ids)).order_by(WorkspaceRow.created_at)).all()
            return [self._workspace(r) for r in rows]

    def members(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._sessions() as s:
            rows = s.scalars(select(MembershipRow).where(MembershipRow.workspace_id == workspace_id)).all()
            out = []
            for m in rows:
                user = s.get(UserRow, m.user_id)
                out.append({"user_id": m.user_id, "role": m.role,
                            "email": user.email if user else "unknown",
                            "name": user.name if user else "", "created_at": _aware(m.created_at)})
            return out

    def role_of(self, workspace_id: str, user_id: str) -> Role | None:
        with self._sessions() as s:
            row = s.get(MembershipRow, (workspace_id, user_id))
            return row.role if row else None  # type: ignore[return-value]

    def add_member(self, workspace_id: str, user_id: str, role: Role = "member") -> Membership:
        with self._lock, self._sessions() as s:
            row = s.get(MembershipRow, (workspace_id, user_id))
            if row is None:
                row = MembershipRow(workspace_id=workspace_id, user_id=user_id, role=role,
                                    created_at=_naive(_utcnow()))
                s.add(row)
            else:
                row.role = role
            s.commit()
            return Membership(workspace_id=workspace_id, user_id=user_id, role=role,
                              created_at=_aware(row.created_at))

    # -- projects ------------------------------------------------------------------ #
    def create_project(self, *, workspace_id: str, name: str, created_by: str,
                       mode: str = "observe", demo: bool = False, project_id: str | None = None) -> Project:
        with self._lock, self._sessions() as s:
            row = ProjectRow(id=project_id or new_id("prj"), workspace_id=workspace_id,
                             name=name.strip() or "Project", slug=slugify(name), mode=mode,
                             collection=dict(DEFAULT_COLLECTION), demo=demo,
                             created_at=_naive(_utcnow()), created_by=created_by)
            s.add(row)
            s.commit()
            return self._project(row)

    def project(self, project_id: str) -> Project | None:
        with self._sessions() as s:
            row = s.get(ProjectRow, project_id)
            return self._project(row) if row else None

    def projects(self, workspace_id: str) -> list[Project]:
        with self._sessions() as s:
            rows = s.scalars(select(ProjectRow).where(ProjectRow.workspace_id == workspace_id)
                             .order_by(ProjectRow.created_at)).all()
            return [self._project(r) for r in rows]

    def projects_for(self, user_id: str) -> list[Project]:
        out: list[Project] = []
        for ws in self.workspaces_for(user_id):
            out.extend(self.projects(ws.id))
        return out

    def update_project(self, project_id: str, **fields: Any) -> Project:
        with self._lock, self._sessions() as s:
            row = s.get(ProjectRow, project_id)
            if row is None:
                raise AccountError("Unknown project")
            if "name" in fields and fields["name"]:
                row.name = str(fields["name"]).strip()
                row.slug = slugify(row.name)
            if "mode" in fields and fields["mode"] in ("observe", "govern", "enforce"):
                row.mode = fields["mode"]
            if "collection" in fields and isinstance(fields["collection"], dict):
                merged = {**DEFAULT_COLLECTION, **(row.collection or {})}
                for key, value in fields["collection"].items():
                    if key in DEFAULT_COLLECTION:
                        merged[key] = bool(value)
                row.collection = merged
            s.commit()
            return self._project(row)

    def delete_project(self, project_id: str) -> None:
        with self._lock, self._sessions() as s:
            row = s.get(ProjectRow, project_id)
            if row is not None:
                s.delete(row)
            for key in s.scalars(select(ApiKeyRow).where(ApiKeyRow.project_id == project_id)).all():
                s.delete(key)
            for integration in s.scalars(select(IntegrationRow).where(IntegrationRow.project_id == project_id)).all():
                s.delete(integration)
            s.commit()

    # -- api keys -------------------------------------------------------------------- #
    def create_key(self, *, project_id: str, name: str, created_by: str, environment: str = "live",
                   scopes: list[str] | None = None, expires_at: datetime | None = None) -> tuple[ApiKey, str]:
        """Returns (record, plaintext). The plaintext is shown exactly once."""
        plaintext, prefix, last4 = generate_api_key(environment)  # type: ignore[arg-type]
        with self._lock, self._sessions() as s:
            row = ApiKeyRow(id=new_id("key"), project_id=project_id, name=name.strip() or "api key",
                            prefix=prefix, last4=last4,
                            key_hash=hash_api_key(plaintext, self._key_secret), environment=environment,
                            scopes=scopes or (["manage"] if environment == "mgmt" else ["ingest", "authorize"]),
                            created_at=_naive(_utcnow()), created_by=created_by,
                            expires_at=_naive(expires_at))
            s.add(row)
            s.commit()
            return self._key(row), plaintext

    def keys(self, project_id: str) -> list[ApiKey]:
        with self._sessions() as s:
            rows = s.scalars(select(ApiKeyRow).where(ApiKeyRow.project_id == project_id)
                             .order_by(ApiKeyRow.created_at.desc())).all()
            return [self._key(r) for r in rows]

    def key(self, key_id: str) -> ApiKey | None:
        with self._sessions() as s:
            row = s.get(ApiKeyRow, key_id)
            return self._key(row) if row else None

    def resolve_key(self, plaintext: str, *, touch: bool = True) -> ApiKey | None:
        """Authenticate a presented key. Revoked and expired keys resolve to None."""
        digest = hash_api_key(plaintext, self._key_secret)
        with self._sessions() as s:
            row = s.scalar(select(ApiKeyRow).where(ApiKeyRow.key_hash == digest))
            if row is None or row.revoked_at is not None:
                return None
            if row.expires_at is not None and _aware(row.expires_at) <= _utcnow():
                return None
            if touch:
                row.last_used_at = _naive(_utcnow())
                s.commit()
            return self._key(row)

    def rotate_key(self, key_id: str, *, created_by: str) -> tuple[ApiKey, str]:
        with self._lock, self._sessions() as s:
            row = s.get(ApiKeyRow, key_id)
            if row is None:
                raise AccountError("Unknown key")
            row.revoked_at = _naive(_utcnow())
            project_id, name, environment, scopes = row.project_id, row.name, row.environment, list(row.scopes or [])
            s.commit()
        return self.create_key(project_id=project_id, name=name, created_by=created_by,
                               environment=environment, scopes=scopes)

    def revoke_key(self, key_id: str) -> ApiKey | None:
        with self._lock, self._sessions() as s:
            row = s.get(ApiKeyRow, key_id)
            if row is None:
                return None
            row.revoked_at = _naive(_utcnow())
            s.commit()
            return self._key(row)

    # -- integrations ------------------------------------------------------------------- #
    def upsert_integration(self, *, project_id: str, kind: str, name: str | None = None,
                           host: str | None = None, config: dict[str, Any] | None = None,
                           integration_id: str | None = None) -> Integration:
        with self._lock, self._sessions() as s:
            row = None
            if integration_id:
                row = s.get(IntegrationRow, integration_id)
            if row is None:
                stmt = select(IntegrationRow).where(IntegrationRow.project_id == project_id,
                                                    IntegrationRow.kind == kind)
                if host:
                    stmt = stmt.where(IntegrationRow.host == host)
                row = s.scalars(stmt).first()
            if row is None:
                row = IntegrationRow(id=new_id("int"), project_id=project_id, kind=kind,
                                     name=name or kind, host=host, status="pending",
                                     config=config or {}, agents=[], actions=0,
                                     created_at=_naive(_utcnow()))
                s.add(row)
            else:
                if name:
                    row.name = name
                if host:
                    row.host = host
                if config:
                    row.config = {**(row.config or {}), **config}
            s.commit()
            return self._integration(row)

    def touch_integration(self, *, project_id: str, kind: str, host: str | None,
                          agent: str | None, actions: int = 1) -> Integration:
        """Record live activity from a connector; this is what flips it to CONNECTED."""
        with self._lock, self._sessions() as s:
            stmt = select(IntegrationRow).where(IntegrationRow.project_id == project_id,
                                                IntegrationRow.kind == kind)
            if host:
                stmt = stmt.where(IntegrationRow.host == host)
            row = s.scalars(stmt).first()
            if row is None:
                row = IntegrationRow(id=new_id("int"), project_id=project_id, kind=kind,
                                     name=kind, host=host, status="connected", config={},
                                     agents=[], actions=0, created_at=_naive(_utcnow()))
                s.add(row)
            row.status = "connected"
            row.last_seen_at = _naive(_utcnow())
            row.actions = int(row.actions or 0) + actions
            if agent and agent not in (row.agents or []):
                row.agents = [*(row.agents or []), agent]
            if host and not row.host:
                row.host = host
            s.commit()
            return self._integration(row)

    def integrations(self, project_id: str) -> list[Integration]:
        with self._sessions() as s:
            rows = s.scalars(select(IntegrationRow).where(IntegrationRow.project_id == project_id)
                             .order_by(IntegrationRow.created_at)).all()
            return [self._integration(r) for r in rows]

    def delete_integration(self, integration_id: str) -> None:
        with self._lock, self._sessions() as s:
            row = s.get(IntegrationRow, integration_id)
            if row is not None:
                s.delete(row)
                s.commit()

    # -- demo ------------------------------------------------------------------------- #
    def ensure_demo_project(self) -> Project:
        """The hosted demo runs in its own project, owned by nobody."""
        existing = self.project(DEMO_PROJECT_ID)
        if existing is not None:
            return existing
        with self._lock, self._sessions() as s:
            if s.get(WorkspaceRow, DEMO_WORKSPACE_ID) is None:
                s.add(WorkspaceRow(id=DEMO_WORKSPACE_ID, name="Demo", slug="demo",
                                   created_at=_naive(_utcnow()), created_by="system"))
                s.commit()
        return self.create_project(workspace_id=DEMO_WORKSPACE_ID, name="demo-project",
                                   created_by="system", mode="enforce", demo=True,
                                   project_id=DEMO_PROJECT_ID)


def build_account_store(settings: Settings) -> AccountStore:
    return AccountStore(settings.audit_db_url, settings.api_key_secret)
