"""Resolve an incoming request to *who is acting, for which project*.

Three credential shapes reach the runtime, and only the first is what a
developer normally holds:

* ``Authorization: Bearer ap_live_…`` (or ``X-Api-Key``) - a Project API Key.
  The project is the tenant; the agent, session, and task come from the
  request itself. Nobody mints a JWT by hand.
* ``Authorization: Bearer <jwt>`` - the original identity modes
  (``jwt_claims`` / ``delegation``), kept intact for existing deployments and
  for issuers that verify scope cryptographically.
* A console session cookie - humans, handled in the accounts router; it never
  authorizes an agent action.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from agent_plane.accounts.models import Project
from agent_plane.accounts.security import looks_like_api_key
from agent_plane.config import Settings
from agent_plane.gateway.identity import IdentityError, resolve_identity
from agent_plane.schemas.canonical import Actor

# A connector that names no agent still needs one; the integration is the agent.
DEFAULT_AGENT = "agent"


@dataclass
class RequestContext:
    actor: Actor
    project: Project | None = None
    api_key_id: str | None = None
    integration: str | None = None      # integration kind: claude-code, mcp, ...
    host: str | None = None             # machine/workspace the connector runs on
    session: str | None = None
    scopes: tuple[str, ...] = ()

    @property
    def tenant(self) -> str:
        return self.actor.tenant

    @property
    def agent(self) -> str:
        return self.actor.agent_id or self.actor.user_id

    def requires(self, scope: str) -> None:
        if self.api_key_id and self.scopes and scope not in self.scopes:
            raise HTTPException(status_code=403, detail=f"this API key lacks the '{scope}' scope")


def _presented_key(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key and looks_like_api_key(x_api_key.strip()):
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if looks_like_api_key(token):
            return token
    return None


def resolve_request(
    request: Request,
    *,
    authorization: str | None = None,
    x_api_key: str | None = None,
    body: dict[str, Any] | None = None,
) -> RequestContext:
    """Authenticate the caller and work out which project it acts in."""
    settings: Settings = request.app.state.settings
    body = body or {}
    accounts = getattr(request.app.state, "accounts", None)

    presented = _presented_key(authorization, x_api_key)
    if presented and accounts is not None:
        key = accounts.resolve_key(presented)
        if key is None:
            raise HTTPException(status_code=401, detail="Invalid or revoked API key")
        project = accounts.project(key.project_id)
        if project is None:
            raise HTTPException(status_code=401, detail="This key's project no longer exists")

        header = request.headers
        agent = (body.get("agent") or header.get("X-Agent-Id") or "").strip()
        integration = (body.get("integration") or header.get("X-Integration") or "").strip() or None
        host = (body.get("host") or header.get("X-Agent-Host") or "").strip() or None
        session = (body.get("session") or header.get("X-Session-Id") or "").strip() or None
        capabilities = body.get("capabilities") or []
        actor = Actor(
            user_id=key.id,
            tenant=project.id,
            app_id=integration,
            agent_id=agent or integration or DEFAULT_AGENT,
            allowed_tools=[c for c in capabilities if isinstance(c, str)],
        )
        return RequestContext(actor=actor, project=project, api_key_id=key.id,
                              integration=integration, host=host, session=session,
                              scopes=tuple(key.scopes))

    try:
        actor = resolve_identity(authorization, settings, request.app.state.revocations)
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    project = accounts.project(actor.tenant) if accounts is not None else None
    return RequestContext(actor=actor, project=project,
                          integration=(body.get("integration") or request.headers.get("X-Integration") or None),
                          host=body.get("host") or request.headers.get("X-Agent-Host"),
                          session=body.get("session") or request.headers.get("X-Session-Id"))
