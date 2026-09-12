"""FastAPI application factory + lifespan wiring.

On startup the control-plane components are built once and attached to
``app.state``: the policy bundle is loaded, the model registry is constructed,
and the cache/audit/authority stores are initialized per the configured
backend. In ``environment=production`` the startup is fail-closed (no default
secrets, no process-local authority store).
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from importlib.resources import files

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from agent_plane.approvals.notify import ApprovalNotifier
from agent_plane.approvals.store import build_approval_store
from agent_plane.audit.store import build_audit_store
from agent_plane.authority.store import build_lease_store
from agent_plane.authority.templates import build_template_catalog
from agent_plane.cache.store import build_cache_store
from agent_plane.config import Settings, get_settings
from agent_plane.gateway.a2a import a2a_router
from agent_plane.gateway.admin import admin_router
from agent_plane.gateway.approvals import approvals_router
from agent_plane.gateway.authority import authority_router
from agent_plane.gateway.broker import broker_router
from agent_plane.gateway.retrieval import retrieval_router
from agent_plane.gateway.router import router
from agent_plane.gateway.usage_api import usage_router
from agent_plane.observability import Metrics, configure_logging
from agent_plane.policy.engine import YamlPolicyEngine
from agent_plane.policy.loader import load_bundle
from agent_plane.routing.knowledge import build_knowledge_store
from agent_plane.routing.registry import ModelRegistry
from agent_plane.routing.tools import build_tool_registry
from agent_plane.usage.store import build_usage_store

logger = logging.getLogger("agent_plane")


def _installed_version() -> str:
    try:
        return package_version("agent-plane")
    except PackageNotFoundError:
        return "0.0.0+local"


def _configure_logging(settings: Settings) -> None:
    configure_logging(settings.log_level, settings.log_format)


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
        if settings.environment == "production":
            raise RuntimeError(
                "Refusing to start in production with no policies loaded (ALLOW-ALL) - "
                "run `agentplane init` or set POLICY_DIR."
            )
        logger.warning(
            "No policies loaded - running ALLOW-ALL. Run `agentplane init` or set POLICY_DIR."
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
    app.state.leases = build_lease_store(settings)
    app.state.lease_templates = build_template_catalog(settings)
    app.state.approvals = build_approval_store(settings)
    app.state.approval_notifier = ApprovalNotifier(
        settings.approval_webhook_url, settings.audit_signing_key
    )
    app.state.cache = build_cache_store(settings)
    app.state.audit = build_audit_store(settings)
    app.state.usage = build_usage_store(settings)
    if not hasattr(app.state, "metrics"):
        app.state.metrics = Metrics()
    # Runtime revocation set, mutated live by the admin API.
    app.state.revocations = set()

    logger.info(
        "agent-plane ready: version=%s env=%s identity=%s backend=%s authority_store=%s "
        "policy_version=%s",
        _installed_version(), settings.environment, settings.identity_mode,
        settings.storage_backend, settings.authority_store, bundle.version,
    )
    if getattr(app.state, "mcp_app", None) is not None:
        async with app.state.mcp_app.router.lifespan_context(app.state.mcp_app):
            yield
    else:
        yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="agent-plane - runtime authority for AI agents",
        version=_installed_version(),
        lifespan=lifespan,
    )
    app.state.metrics = Metrics()

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
            app.state.metrics.observe_request(
                request.method, request.url.path, 500, time.perf_counter() - started)
            return JSONResponse(
                status_code=500,
                content={"error": "internal_error", "request_id": request_id},
                headers={"X-Request-ID": request_id},
            )
        elapsed = time.perf_counter() - started
        app.state.metrics.observe_request(
            request.method, request.url.path, response.status_code, elapsed)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "%s %s -> %s %dms req_id=%s",
            request.method, request.url.path, response.status_code, int(elapsed * 1000), request_id,
            extra={"method": request.method, "path": request.url.path,
                   "status": response.status_code, "latency_ms": int(elapsed * 1000),
                   "request_id": request_id},
        )
        return response

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/console")

    @app.get("/console", include_in_schema=False)
    async def console() -> HTMLResponse:
        html = (files("agent_plane.console") / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    @app.get("/console/assets/{name}", include_in_schema=False)
    async def console_asset(name: str) -> Response:
        media_types = {"console.css": "text/css", "console.js": "text/javascript"}
        if name not in media_types:
            raise HTTPException(status_code=404, detail="not found")
        content = (files("agent_plane.console") / name).read_text(encoding="utf-8")
        return Response(content, media_type=media_types[name])
    @app.get("/flow", include_in_schema=False)
    async def integration_flow() -> HTMLResponse:
        html = (files("agent_plane.console") / "flow.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        # Ready only if the audit and authority stores are reachable.
        try:
            app.state.audit.recent(limit=1)
            app.state.leases.list()
            return JSONResponse({"status": "ready"})
        except Exception as exc:  # noqa: BLE001
            logger.exception("readiness check failed")
            return JSONResponse({"status": "not_ready", "error": str(exc)}, status_code=503)

    if settings.metrics_enabled:
        @app.get("/metrics", include_in_schema=False)
        async def metrics():
            return app.state.metrics.response()

    app.include_router(router)
    app.include_router(broker_router)
    app.include_router(retrieval_router)
    app.include_router(a2a_router)
    app.include_router(authority_router)
    app.include_router(approvals_router)
    app.include_router(usage_router)
    app.include_router(admin_router)
    if settings.mcp_gateway_file:
        from starlette.routing import Route

        from agent_plane.gateway.mcp import build_gateway

        app.state.gateway_body_limit = settings.max_request_bytes or 1_000_000
        app.state.mcp_app, endpoint = build_gateway(app, settings.mcp_gateway_file)
        # A callable ASGI object avoids Starlette interpreting it as request -> response.
        class MCPRoute:
            async def __call__(self, scope, receive, send):
                await endpoint(scope, receive, send)

        app.router.routes.append(Route("/mcp", endpoint=MCPRoute()))
    return app


app = create_app()
