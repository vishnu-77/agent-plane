"""Who is allowed to read or change what.

Three ways to be an operator, in the order they are tried:

1. **A console session** (signed cookie) plus a project the user belongs to.
   This is how a human uses the product; no token is ever pasted into the UI.
2. **A management API key** (``ap_mgmt_``) scoped to one project, for
   automation.
3. **``ADMIN_TOKEN``**, which still sees every tenant. It is the deployment's
   break-glass credential and an implementation detail of self-hosting, not
   something the product asks a developer for.

The hosted demo adds a fourth, read-mostly path: ``X-Demo-Token`` scoped to
the demo project alone.
"""
from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from agent_plane.accounts.security import SESSION_COOKIE, looks_like_api_key, read_session
from agent_plane.config import Settings

DEMO_TENANT = "prj_demo"


class OperatorScope:
    """The tenants a caller may read, and who they are."""

    def __init__(self, tenant: str | None, *, demo: bool = False, user_id: str | None = None,
                 can_write: bool = True, source: str = "admin"):
        self.tenant = tenant          # None = every tenant
        self.demo = demo
        self.user_id = user_id
        self.can_write = can_write
        self.source = source          # session | mgmt_key | admin | demo

    def restrict(self, requested: str | None) -> str | None:
        """Apply the scope to a caller-requested tenant filter."""
        if self.tenant is None:
            return requested
        return self.tenant

    def allows(self, tenant: str) -> bool:
        return self.tenant is None or tenant == self.tenant

    def require_write(self) -> None:
        if not self.can_write:
            raise HTTPException(status_code=403, detail="This view is read-only")


def _session_user(request: Request) -> str | None:
    settings: Settings = request.app.state.settings
    payload = read_session(request.cookies.get(SESSION_COOKIE), settings.api_key_secret)
    return str(payload["sub"]) if payload and payload.get("sub") else None


def _project_scope_for_user(request: Request, user_id: str, requested: str | None) -> OperatorScope:
    accounts = request.app.state.accounts
    projects = accounts.projects_for(user_id)
    if not projects:
        raise HTTPException(status_code=404, detail="You have no projects yet")
    chosen = next((p for p in projects if p.id == requested), None) if requested else projects[0]
    if chosen is None:
        raise HTTPException(status_code=403, detail="You do not have access to that project")
    return OperatorScope(chosen.id, user_id=user_id, source="session")


def resolve_operator(request: Request, x_admin_token: str | None,
                     x_demo_token: str | None = None,
                     project: str | None = None) -> OperatorScope:
    settings: Settings = request.app.state.settings
    accounts = getattr(request.app.state, "accounts", None)

    # 0. The demo project, whoever is asking. The console keeps a LIVE/DEMO
    #    toggle while signed in, so a demo read arrives with a session cookie
    #    attached; resolving that session first would scope the caller to their
    #    own project and refuse the demo tenant they actually asked for.
    wants_demo = (project or request.query_params.get("project")
                  or request.query_params.get("tenant")) == DEMO_TENANT
    if (wants_demo and settings.demo_enabled and x_demo_token
            and hmac.compare_digest(x_demo_token, settings.demo_token)):
        return OperatorScope(DEMO_TENANT, demo=True, can_write=False, source="demo")

    # 1. A signed-in human, scoped to one of their projects.
    if accounts is not None:
        user_id = _session_user(request)
        if user_id and accounts.user(user_id) is not None:
            return _project_scope_for_user(request, user_id, project or request.query_params.get("project"))

    # 2. A management key, scoped to its own project.
    presented = x_admin_token or ""
    if accounts is not None and looks_like_api_key(presented):
        key = accounts.resolve_key(presented)
        if key is not None and key.environment == "mgmt":
            return OperatorScope(key.project_id, source="mgmt_key")
        raise HTTPException(status_code=401, detail="Invalid management key")

    # 3. The deployment's break-glass admin token: every tenant.
    if settings.admin_token and hmac.compare_digest(presented, settings.admin_token):
        return OperatorScope(None, source="admin")

    # 4. The hosted demo viewer: the demo project, read-only.
    if settings.demo_enabled and x_demo_token and hmac.compare_digest(x_demo_token, settings.demo_token):
        return OperatorScope(DEMO_TENANT, demo=True, can_write=False, source="demo")

    if not settings.admin_token and not settings.demo_enabled:
        raise HTTPException(status_code=404, detail="not found")
    raise HTTPException(status_code=401, detail="Sign in to view this workspace")


def require_admin(request: Request, x_admin_token: str | None) -> None:
    """Gate a deployment-level operation behind ADMIN_TOKEN or a management key.

    Used for the endpoints that are genuinely deployment-wide (policy reload,
    credential revocation). Project-level operations use ``resolve_operator``.
    """
    settings: Settings = request.app.state.settings
    accounts = getattr(request.app.state, "accounts", None)
    presented = x_admin_token or ""
    if accounts is not None and looks_like_api_key(presented):
        key = accounts.resolve_key(presented)
        if key is not None and key.environment == "mgmt":
            return
    # A console session counts only for the account that set the instance up.
    # These are deployment-wide operations, not project ones, so on a shared
    # instance a second account must not inherit them.
    user_id = _session_user(request) if accounts is not None else None
    if user_id and accounts.is_instance_owner(user_id):
        return
    if user_id:
        raise HTTPException(status_code=403,
                            detail="This is a deployment-wide operation; it needs the instance owner, "
                                   "a management key, or ADMIN_TOKEN")
    if not settings.admin_token:
        raise HTTPException(status_code=404, detail="not found")
    if not hmac.compare_digest(presented, settings.admin_token):
        raise HTTPException(status_code=401, detail="invalid admin token")
