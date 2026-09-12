"""Admin API: live revocation + policy hot-reload.

Disabled unless ``ADMIN_TOKEN`` is configured. When enabled, callers must present
the token in the ``X-Admin-Token`` header. Lets an operator revoke an agent
credential or reload policies without restarting the control plane.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from agent_plane.config import Settings
from agent_plane.gateway.authz import require_admin as _require_admin
from agent_plane.policy.engine import YamlPolicyEngine
from agent_plane.policy.loader import load_bundle

admin_router = APIRouter(prefix="/admin", tags=["admin"])


def _audit_admin(request: Request, action: str, detail: str, policy_version: str | None) -> None:
    """Admin mutations are security-sensitive (they can silently un-revoke a
    credential or swap the policy bundle) - they get the same signed audit chain
    as every other edge, not a free pass because the caller holds ADMIN_TOKEN."""
    request.app.state.audit.record({
        "decision_id": f"admin_{uuid.uuid4().hex[:12]}",
        "user_id": "admin",
        "tenant": "*",
        "model_requested": f"admin:{action}",
        "model_used": action,
        "data_classification": "",
        "decision": "admin_action",
        "reason": detail,
        "policy_version": policy_version,
        "rules_matched": [],
    })


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
    _audit_admin(request, "revoke_add", f"revoked jti={jti}",
                 request.app.state.engine.bundle.version)
    return {"revoked": sorted(request.app.state.revocations)}


@admin_router.delete("/revocations/{jti}")
async def remove_revocation(
    request: Request, jti: str, x_admin_token: str | None = Header(default=None)
) -> dict[str, Any]:
    _require_admin(request, x_admin_token)
    request.app.state.revocations.discard(jti)
    _audit_admin(request, "revoke_remove", f"un-revoked jti={jti}",
                 request.app.state.engine.bundle.version)
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
        "policies": [p.model_dump(mode="json") for p in engine.bundle.policies],
    }


@admin_router.get("/gateway")
async def gateway_catalog(
    request: Request, x_admin_token: str | None = Header(default=None)
) -> dict[str, Any]:
    """Explicitly allowlisted operational metadata; never credentials or documents."""
    _require_admin(request, x_admin_token)
    state = request.app.state
    settings: Settings = state.settings
    configured = {
        "openai": bool(settings.openai_api_key),
        "anthropic": bool(settings.anthropic_api_key),
        "azure": bool(settings.azure_openai_api_key and settings.azure_openai_endpoint),
    }
    models = []
    for model_id in state.registry.list_models():
        entry = state.registry.resolve(model_id)
        models.append({
            "id": entry.model_id, "provider": entry.provider,
            "upstream_model": entry.upstream_model, "tags": sorted(entry.tags),
            "fallback": list(entry.fallback),
            "credentials_configured": configured.get(entry.provider, False),
        })
    tools = []
    for name in state.tools.list():
        spec = state.tools.get(name)
        tools.append({"name": name, "type": spec.type, "method": spec.method})
    return {
        "environment": settings.environment,
        "identity_mode": settings.identity_mode,
        "storage_backend": settings.storage_backend,
        "lease_storage": "sql" if getattr(state.leases, "durable", False) else "in_memory",
        "models": models,
        "tools": tools,
        "knowledge": [
            {"name": name, "type": state.knowledge.get(name).type}
            for name in state.knowledge.list()
        ],
    }


@admin_router.get("/leases")
async def list_leases(
    request: Request, x_admin_token: str | None = Header(default=None)
) -> dict[str, Any]:
    """Operator inventory across tenants, including instance-local use counts."""
    _require_admin(request, x_admin_token)
    store = request.app.state.leases
    now = datetime.now(UTC)
    items = []
    for lease in store.list():
        expiry = lease.expires_at
        # Legacy manifests may omit a timezone; display them consistently as UTC.
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        status = "revoked" if lease.revoked else (
            "expired" if expiry is not None and expiry <= now else "active"
        )
        items.append({
            **lease.model_dump(mode="json"), "status": status,
            "uses": {action: store.use_count(lease.id, action) for action in lease.actions},
        })
    return {"items": items, "storage": "sql" if getattr(store, "durable", False) else "in_memory"}


@admin_router.post("/policies/reload")
async def reload_policies(
    request: Request, x_admin_token: str | None = Header(default=None)
) -> dict[str, Any]:
    _require_admin(request, x_admin_token)
    settings: Settings = request.app.state.settings
    bundle = load_bundle(settings.policy_dir)
    if settings.environment == "production" and not bundle.policies:
        raise HTTPException(status_code=400, detail="Cannot activate an empty policy bundle in production")
    # Hot-swap the engine; the router reads app.state.engine per request.
    request.app.state.engine = YamlPolicyEngine(
        bundle, provider_resolver=request.app.state.registry.provider_tags
    )
    _audit_admin(request, "policy_reload", f"policy bundle reloaded -> {bundle.version}",
                 bundle.version)
    return {
        "reloaded": True,
        "policy_version": bundle.version,
        "rules": [p.name for p in bundle.policies],
    }
