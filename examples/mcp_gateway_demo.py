"""Run a real MCP client -> agent-plane -> mock MCP upstream demonstration.

python examples/mcp_gateway_demo.py --serve
Only the local mock server is invoked. No repository or cloud operation occurs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import yaml


def configuration(upstream_port):
    return {
        "upstream_url": f"http://127.0.0.1:{upstream_port}/mcp",
        "upstream_secret_env": "MOCK_UPSTREAM_SECRET",
        "bindings": [{"tenant": "acme", "agent": "repo-agent", "task": "remediate-stale-branches", "lease": "lease-mcp-cleanup"}],
        "tools": [
            {"name": "repo.branches", "upstream_tool": "list_branches", "action": "branch.list",
             "resource": "git://acme/demo/branches", "description": "List branch names in the configured demo repository.",
             "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "repo.delete_branch", "upstream_tool": "delete_branch", "action": "branch.delete",
             "resource": "git://acme/demo/branches/{branch}", "description": "Request branch deletion. Protected branches are denied; other deletions require approval.",
             "input_schema": {"type": "object", "properties": {"branch": {"type": "string", "minLength": 1, "maxLength": 128}},
                              "required": ["branch"], "additionalProperties": False}},
        ],
    }


def lease_data():
    return {"id": "lease-mcp-cleanup", "subject": "repo-agent", "tenant": "acme", "task": "remediate-stale-branches",
            "actions": ["branch.list", "branch.delete"], "resources": ["git://acme/demo/branches", "git://acme/demo/branches/*"],
            "protected_resources": ["git://acme/demo/branches/main"], "require_approval": ["branch.delete"],
            "max_uses": {"branch.list": 20, "branch.delete": 5}, "child_authority": "none",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}


def mock_server(port):
    import uvicorn
    from mcp.server import MCPServer
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    server = MCPServer("agent-plane mock repository")
    calls = []

    @server.tool()
    def list_branches() -> dict:
        """List mock branch names, without contacting GitHub."""
        calls.append("list_branches")
        return {"branches": ["main", "stale-fix"], "execution": "local mock"}

    @server.tool()
    def delete_branch(branch: str) -> dict:
        """Record a mock delete, without changing a repository."""
        calls.append("delete_branch")
        return {"branch": branch, "execution": "local mock"}

    app = server.streamable_http_app(stateless_http=True, json_response=True)

    async def stats(request):
        return JSONResponse({"calls": calls})

    app.routes.append(Route("/stats", stats))

    class AuthenticatedMock:
        async def __call__(self, scope, receive, send):
            if scope["type"] == "http" and Request(scope).headers.get("authorization") != "Bearer " + os.environ["MOCK_UPSTREAM_SECRET"]:
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
            await app(scope, receive, send)

    uvicorn.run(AuthenticatedMock(), host="127.0.0.1", port=port, log_level="warning")


async def exercise(url, token):
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    results = []
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + token}, trust_env=False) as http:
        async with Client(streamable_http_client(url + "/mcp", http_client=http), mode="2026-07-28", cache=None) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools.tools} == {"repo.branches", "repo.delete_branch"}
            for name, arguments, expected in [
                ("repo.branches", {}, "completed"),
                ("repo.delete_branch", {"branch": "stale-fix"}, "not_dispatched"),
                ("repo.delete_branch", {"branch": "main"}, "not_dispatched"),
            ]:
                reply = await client.call_tool(name, arguments, meta={"agent-plane/request-id": secrets.token_hex(16)})
                evidence = (reply.meta or {}).get("agent-plane")
                assert evidence and evidence["execution_status"] == expected, reply
                results.append({"tool": name, "arguments": arguments, "is_error": reply.is_error,
                                "evidence": evidence, "content": [c.model_dump(mode="json", by_alias=True) for c in reply.content]})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--upstream-port", type=int, default=8781)
    parser.add_argument("--serve", action="store_true", help="Keep the demonstration console running until interrupted")
    parser.add_argument("--mock-upstream", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.mock_upstream:
        mock_server(args.upstream_port)
        return
    directory = Path(tempfile.mkdtemp(prefix="agent-plane-mcp-demo-"))
    (directory / "gateway.yaml").write_text(yaml.safe_dump(configuration(args.upstream_port)), encoding="utf-8")
    (directory / "leases.yaml").write_text(yaml.safe_dump({"leases": [lease_data()]}), encoding="utf-8")
    env = os.environ.copy()
    env.update({"ENVIRONMENT": "development", "STORAGE_BACKEND": "local", "IDENTITY_MODE": "jwt_claims",
                "SQLITE_PATH": str(directory / "audit.db"), "LEASES_FILE": str(directory / "leases.yaml"),
                "MCP_GATEWAY_FILE": str(directory / "gateway.yaml"), "JWT_SECRET": secrets.token_urlsafe(32),
                "ADMIN_TOKEN": secrets.token_urlsafe(32), "AUDIT_SIGNING_KEY": secrets.token_urlsafe(32),
                "MOCK_UPSTREAM_SECRET": secrets.token_urlsafe(32)})
    processes, logs = [], []
    url = f"http://127.0.0.1:{args.port}"
    try:
        commands = [
            [sys.executable, str(Path(__file__).resolve()), "--mock-upstream", "--upstream-port", str(args.upstream_port)],
            [sys.executable, "-m", "agent_plane.cli", "serve", "--host", "127.0.0.1", "--port", str(args.port)],
        ]
        for index, command in enumerate(commands):
            log = (directory / f"server-{index}.log").open("w", encoding="utf-8")
            logs.append(log)
            processes.append(subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT))
        with httpx.Client(timeout=2, trust_env=False) as http:
            for _ in range(100):
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError(f"Demo process stopped; inspect {directory}")
                try:
                    if http.get(url + "/readyz").status_code == 200 and http.get(f"http://127.0.0.1:{args.upstream_port}/stats",
                        headers={"Authorization": "Bearer " + env["MOCK_UPSTREAM_SECRET"]}).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError(f"Demo did not start; inspect {directory}")
            token = jwt.encode({"sub": "demo-operator", "tenant": "acme", "agent_id": "repo-agent",
                                "allowed_tools": ["branch", "repo.branches", "repo.delete_branch"],
                                "exp": datetime.now(UTC) + timedelta(hours=1)}, env["JWT_SECRET"], algorithm="HS256")
            results = asyncio.run(exercise(url, token))
            stats = http.get(f"http://127.0.0.1:{args.upstream_port}/stats", headers={"Authorization": "Bearer " + env["MOCK_UPSTREAM_SECRET"]}).json()
            assert stats["calls"] == ["list_branches"], stats
            events = http.get(url + "/v1/audit?limit=50", headers={"X-Admin-Token": env["ADMIN_TOKEN"]}).json()["events"]
            assert {r['decision'] for r in events if r['model_requested'].startswith('authorize:')} == {'allow', 'deny', 'approval_required'}
        (directory / "results.json").write_text(json.dumps({"results": results, "upstream": stats}, indent=2), encoding="utf-8")
        print("PASS: real MCP discovery, ALLOW forwarded once, APPROVAL and DENY never forwarded", flush=True)
        print(f"Console: {url}/console\nEvidence: {directory / 'results.json'}", flush=True)
        if args.serve:
            print(f"Local console ADMIN_TOKEN: {env['ADMIN_TOKEN']}", flush=True)
            print("Connect operator access with this token; open Decisions and Timeline. Ctrl+C stops both demo processes.", flush=True)
            while all(p.poll() is None for p in processes):
                time.sleep(1)
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
