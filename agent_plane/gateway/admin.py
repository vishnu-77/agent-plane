"""Admin API: live revocation + policy hot-reload.

Disabled unless ``ADMIN_TOKEN`` is configured. When enabled, callers must present
the token in the ``X-Admin-Token`` header. Lets an operator revoke an agent
credential or reload policies without restarting the control plane.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from agent_plane.config import Settings
from agent_plane.gateway.authz import require_admin as _require_admin
from agent_plane.policy.engine import YamlPolicyEngine
from agent_plane.policy.loader import load_bundle

admin_router = APIRouter(prefix="/admin", tags=["admin"])


@admin_router.get("/revocations")
async def list_revocations(
    request: Request, x_admin_token: str | None = Header(default=None)
) -> dict[str, Any]:
    _require_admin(request, x_admin_token)
    return {"revoked": sorted(request.app.state.revocations)}


@admin_router.post("/revocations")
async def add_revocation(
    request: Request,
    body: dict[str, str],
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_admin_token)
    jti = (body or {}).get("jti")
    if not jti:
        raise HTTPException(status_code=400, detail="missing 'jti'")
    request.app.state.revocations.add(jti)
    return {"revoked": sorted(request.app.state.revocations)}


@admin_router.delete("/revocations/{jti}")
async def remove_revocation(
    request: Request, jti: str, x_admin_token: str | None = Header(default=None)
) -> dict[str, Any]:
    _require_admin(request, x_admin_token)
    request.app.state.revocations.discard(jti)
    return {"revoked": sorted(request.app.state.revocations)}


@admin_router.get("/policies")
async def show_policies(
    request: Request, x_admin_token: str | None = Header(default=None)
) -> dict[str, Any]:
    _require_admin(request, x_admin_token)
    engine: YamlPolicyEngine = request.app.state.engine
    return {
        "policy_version": engine.bundle.version,
        "rules": [p.name for p in engine.bundle.policies],
    }


@admin_router.post("/policies/reload")
async def reload_policies(
    request: Request, x_admin_token: str | None = Header(default=None)
) -> dict[str, Any]:
    _require_admin(request, x_admin_token)
    settings: Settings = request.app.state.settings
    bundle = load_bundle(settings.policy_dir)
    # Hot-swap the engine; the router reads app.state.engine per request.
    request.app.state.engine = YamlPolicyEngine(
        bundle, provider_resolver=request.app.state.registry.provider_tags
    )
    return {
        "reloaded": True,
        "policy_version": bundle.version,
        "rules": [p.name for p in bundle.policies],
    }
