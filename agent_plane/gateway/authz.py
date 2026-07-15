"""Shared authorization helpers."""
from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from agent_plane.config import Settings


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
