"""FastAPI application factory + lifespan wiring.

On startup the control-plane components are built once and attached to
``app.state``: the policy bundle is loaded, the model registry is constructed,
and the cache/audit stores are initialized per the configured backend. In
``environment=production`` the startup is fail-closed (no default secrets).
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from importlib.resources import files

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from agent_plane.audit.store import build_audit_store
from agent_plane.cache.store import build_cache_store
from agent_plane.config import Settings, get_settings
from agent_plane.gateway.a2a import a2a_router
from agent_plane.gateway.admin import admin_router
from agent_plane.gateway.broker import broker_router
from agent_plane.gateway.retrieval import retrieval_router
from agent_plane.gateway.router import router
from agent_plane.gateway.usage_api import usage_router
from agent_plane.policy.engine import YamlPolicyEngine
from agent_plane.policy.loader import load_bundle
from agent_plane.routing.knowledge import build_knowledge_store
from agent_plane.routing.registry import ModelRegistry
from agent_plane.routing.tools import build_tool_registry
from agent_plane.usage.store import build_usage_store

logger = logging.getLogger("agent_plane")


def _configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings)

    # Fail closed in production: never run with default secrets.
    errors = settings.production_errors()
    if errors:
        raise RuntimeError(
            "Refusing to start in production with insecure config: "
            + "; ".join(errors)
        )

    registry = ModelRegistry(settings)
    bundle = load_bundle(settings.policy_dir)
    engine = YamlPolicyEngine(bundle, provider_resolver=registry.provider_tags)

    if not bundle.policies:
        logger.warning(
            "No policies loaded — running ALLOW-ALL. Run `agentplane init` or set POLICY_DIR."
        )
    if settings.environment == "production" and settings.identity_mode == "jwt_claims":
        logger.warning(
            "Production with IDENTITY_MODE=jwt_claims: tokens are trusted as-is. "
            "Prefer IDENTITY_MODE=delegation (verified, scoped, revocable)."
        )

    app.state.settings = settings
    app.state.registry = registry
    app.state.engine = engine
    app.state.tools = build_tool_registry(settings)
    app.state.knowledge = build_knowledge_store(settings)
    app.state.cache = build_cache_store(settings)
    app.state.audit = build_audit_store(settings)
    app.state.usage = build_usage_store(settings)
    # Runtime revocation set, mutated live by the admin API.
    app.state.revocations = set()

    logger.info(
        "agent-plane ready: env=%s identity=%s backend=%s policy_version=%s",
        settings.environment, settings.identity_mode, settings.storage_backend,
        bundle.version,
    )
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="agent-plane — Enterprise Agentic AI Control Plane",
        version="0.1.0",
        lifespan=lifespan,
    )

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def _request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        rid_headers = {"X-Request-ID": request_id}

        # Reject oversized bodies (cheap Content-Length check).
        if settings.max_request_bytes > 0:
            cl = request.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > settings.max_request_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"error": "request_too_large", "request_id": request_id},
                    headers=rid_headers,
                )

        # Per-client rate limit (reuses the cache/quota counter).
        if settings.rate_limit_per_minute > 0:
            client_ip = (request.client.host if request.client else "unknown")
            if settings.trust_forwarded_for:
                fwd = request.headers.get("x-forwarded-for")
                if fwd:
                    client_ip = fwd.split(",")[0].strip()
            used = request.app.state.cache.incr_quota(f"rl:{client_ip}", 1, 60)
            if used > settings.rate_limit_per_minute:
                return JSONResponse(
                    status_code=429,
                    content={"error": "rate_limited", "request_id": request_id},
                    headers={**rid_headers, "Retry-After": "60"},
                )

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 - last-resort guard, logged below
            logger.exception("unhandled error req_id=%s path=%s", request_id, request.url.path)
            return JSONResponse(
                status_code=500,
                content={"error": "internal_error", "request_id": request_id},
                headers={"X-Request-ID": request_id},
            )
        took = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "%s %s -> %s %dms req_id=%s",
            request.method, request.url.path, response.status_code, took, request_id,
        )
        return response

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/console")

    @app.get("/console", include_in_schema=False)
    async def console() -> HTMLResponse:
        html = (files("agent_plane.console") / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        # Ready only if the audit store is reachable (DB connectivity).
        try:
            app.state.audit.recent(limit=1)
            return JSONResponse({"status": "ready"})
        except Exception as exc:  # noqa: BLE001
            logger.exception("readiness check failed")
            return JSONResponse({"status": "not_ready", "error": str(exc)}, status_code=503)

    app.include_router(router)
    app.include_router(broker_router)
    app.include_router(retrieval_router)
    app.include_router(a2a_router)
    app.include_router(usage_router)
    app.include_router(admin_router)
    return app


app = create_app()
