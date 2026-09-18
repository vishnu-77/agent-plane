"""Optional MCP tools gateway using the official SDK, protocol 2026-07-28."""
from __future__ import annotations

import asyncio
import os

import httpx2
from fastapi import HTTPException
from mcp import Client, types
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from starlette.requests import Request
from starlette.responses import JSONResponse

from agent_plane.enforcement.mapping import load_config
from agent_plane.enforcement.service import EnforcementService
from agent_plane.gateway.context import resolve_request
from agent_plane.gateway.identity import IdentityError

PROTOCOL = "2026-07-28"


class BoundedStream(httpx2.AsyncByteStream):
    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > self.limit:
                raise ValueError("MCP upstream response too large")
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class BoundedTransport(httpx2.AsyncBaseTransport):
    def __init__(self, limit):
        self.inner, self.limit = httpx2.AsyncHTTPTransport(retries=0), limit

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        if response.headers.get("content-encoding", "identity") != "identity":
            await response.aclose()
            raise ValueError("Compressed MCP responses are not accepted by the bounded preview")
        return httpx2.Response(response.status_code, headers=response.headers,
                               stream=BoundedStream(response.stream, self.limit), extensions=response.extensions)

    async def aclose(self):
        await self.inner.aclose()


def build_gateway(app, path):
    config = load_config(path)
    if config.upstream_secret_env and not os.environ.get(config.upstream_secret_env):
        raise ValueError("Configured MCP upstream credential is missing")

    def upstream_http():
        headers = {"Accept-Encoding": "identity"}
        if config.upstream_secret_env:
            headers["Authorization"] = "Bearer " + os.environ[config.upstream_secret_env]
        return httpx2.AsyncClient(headers=headers, timeout=config.timeout_seconds,
            trust_env=False, follow_redirects=False, transport=BoundedTransport(config.max_response_bytes))

    async def upstream_call(tool, arguments):
        async with upstream_http() as http:
            async with Client(streamable_http_client(config.upstream_url, http_client=http),
                              mode=PROTOCOL, cache=None, read_timeout_seconds=config.timeout_seconds) as client:
                return await client.call_tool(tool.upstream_tool, arguments)

    service = EnforcementService(app, config, upstream_call)
    app.state.mcp_service = service

    async def list_tools(ctx, params):
        actor = ctx.request.state.agent_plane_actor
        if params and params.cursor:
            raise ValueError("Unexpected gateway cursor")
        # Fresh bounded discovery; no global credential-dependent catalog cache.
        available = {}
        async with asyncio.timeout(config.timeout_seconds):
            async with upstream_http() as http:
                async with Client(streamable_http_client(config.upstream_url, http_client=http),
                                  mode=PROTOCOL, cache=None) as client:
                    cursor = None
                    for _ in range(10):
                        page = await client.list_tools(cursor=cursor)
                        for tool in page.tools:
                            if len(available) >= 500:
                                raise ValueError("Upstream catalog exceeds gateway limit")
                            available[tool.name] = tool
                        cursor = page.next_cursor
                        if not cursor:
                            break
                    else:
                        raise ValueError("Upstream pagination exceeds gateway limit")
        # Publish operator schemas/descriptions, never upstream prompt instructions.
        return types.ListToolsResult(tools=[types.Tool(name=t.name, description=t.description,
                    input_schema=t.input_schema) for t in config.tools
                    if t.upstream_tool in available and service.eligible(actor, t.name)])

    async def call_tool(ctx, params):
        try:
            meta = dict(ctx.meta or {})
            request_key = meta.get("agent-plane/request-id")
            approval_id = meta.get("agent-plane/approval-id")
            result = await service.invoke(ctx.request.state.agent_plane_actor, params.name,
                                          params.arguments or {}, request_key, approval_id)
            evidence = result["evidence"]
            if result["result"] is not None:
                response = types.CallToolResult.model_validate(result["result"])
                # The upstream cannot supply gateway evidence by overwriting _meta.
                response.meta = {"agent-plane": evidence}
                return response
            return types.CallToolResult(is_error=True,
                content=[types.TextContent(type="text", text=f"{result['decision'].upper()}: {result['reason']}. {evidence['execution_status']}.")],
                meta={"agent-plane": evidence})
        except Exception:
            # Do not expose validation values, credentials, URLs or server exceptions.
            return types.CallToolResult(is_error=True, content=[types.TextContent(type="text",
                text="Gateway could not admit this request. No successful execution is confirmed; inspect gateway evidence before retrying.")])

    server = Server("agent-plane", version="0.2.0", on_list_tools=list_tools, on_call_tool=call_tool)
    mcp_app = server.streamable_http_app(streamable_http_path="/mcp", json_response=True,
        stateless_http=True, max_request_body_size=min(app.state.gateway_body_limit, 1_000_000))

    # The operator's declared surface, used as the capability manifest for a
    # caller that authenticates with a Project API Key. Both halves are needed:
    # the policy layer checks the tool name it was asked for, the authority
    # evaluator checks the action that tool performs.
    MAPPED_SURFACE = sorted({t.name for t in config.tools} | {t.action for t in config.tools})

    active_requests = 0

    async def endpoint(scope, receive, send):
        nonlocal active_requests
        request = Request(scope)
        if request.method != "POST":
            await JSONResponse({"error": "method_not_allowed"}, status_code=405, headers={"Allow": "POST"})(scope, receive, send)
            return
        try:
            # A Project API Key is what a developer holds, and what
            # `agentplane connect mcp` hands to the MCP client; the identity
            # modes still work for deployments that mint their own tokens.
            # Either way the (tenant, agent) pair must match a binding in the
            # operator's gateway mapping, so authority is never inferred here.
            ctx = resolve_request(request, authorization=request.headers.get("authorization"),
                                  x_api_key=request.headers.get("x-api-key"))
            actor = ctx.actor
            if ctx.api_key_id and not actor.allowed_tools:
                # An identity token carries a capability manifest its issuer
                # asserts. A Project API Key carries none, and a manifest the
                # client declares about itself would be worth nothing. The
                # operator's tool mapping is the manifest here: this gateway
                # exposes exactly these actions, whoever is calling.
                actor = actor.model_copy(update={"allowed_tools": MAPPED_SURFACE})
            service.binding(actor)
        except (IdentityError, ValueError, HTTPException):
            await JSONResponse({"error": "invalid_identity_or_task_binding"}, status_code=401)(scope, receive, send)
            return
        if request.headers.get("MCP-Protocol-Version") != PROTOCOL:
            await JSONResponse({"error": "unsupported_protocol_version", "supported": [PROTOCOL]}, status_code=400)(scope, receive, send)
            return
        request.state.agent_plane_actor = actor
        if active_requests >= config.max_concurrency:
            await JSONResponse({"error": "gateway_busy"}, status_code=429)(scope, receive, send)
            return
        active_requests += 1
        try:
            await mcp_app(scope, receive, send)
        finally:
            active_requests -= 1

    return mcp_app, endpoint
