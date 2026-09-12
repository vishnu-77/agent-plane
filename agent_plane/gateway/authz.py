"""Shared authorization helpers."""
from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from agent_plane.config import Settings

DEMO_TENANT = "demo"


class OperatorScope:
    """Who is reading: a full operator (all tenants) or the hosted demo viewer
    (the isolated demo tenant only, via X-Demo-Token when DEMO_ENABLED)."""

    def __init__(self, tenant: str | None, *, demo: bool):
        self.tenant = tenant          # None = every tenant
        self.demo = demo

    def restrict(self, requested: str | None) -> str | None:
        """Apply the scope to a caller-requested tenant filter."""
        if self.tenant is None:
            return requested
        return self.tenant

    def allows(self, tenant: str) -> bool:
        return self.tenant is None or tenant == self.tenant


def resolve_operator(request: Request, x_admin_token: str | None,
                     x_demo_token: str | None = None) -> OperatorScope:
    """Admin token grants every tenant; the demo token grants only the demo tenant."""
    settings: Settings = request.app.state.settings
    if settings.admin_token and hmac.compare_digest(x_admin_token or "", settings.admin_token):
        return OperatorScope(None, demo=False)
    if settings.demo_enabled and x_demo_token and hmac.compare_digest(x_demo_token, settings.demo_token):
        return OperatorScope(DEMO_TENANT, demo=True)
    if not settings.admin_token and not settings.demo_enabled:
        raise HTTPException(status_code=404, detail="not found")
    raise HTTPException(status_code=401, detail="invalid operator token")


def require_admin(request: Request, x_admin_token: str | None) -> None:
    """Gate an operator-only endpoint behind the admin token.

    Disabled (404) unless ``ADMIN_TOKEN`` is set; otherwise a constant-time
    comparison guards against timing side-channels.
    """
    settings: Settings = request.app.state.settings
    if not settings.admin_token:
        raise HTTPException(status_code=404, detail="not found")
    if not hmac.compare_digest(x_admin_token or "", settings.admin_token):
        raise HTTPException(status_code=401, detail="invalid admin token")
