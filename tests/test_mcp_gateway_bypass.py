"""Phase 30: bypass path. Compares

    Claude -> MCP client -> Agent Plane gateway -> mock upstream   (governed)
    Claude -> MCP client --------------------------> mock upstream  (direct)

Agent Plane must never claim to protect an action it has no chokepoint
over - if the upstream credential is available outside the gateway,
direct access is an UNCONTROLLED PATH, not a bypass Agent Plane silently
stops. This test proves both halves against real subprocess servers (the
same pattern examples/mcp_gateway_demo.py already uses and this suite
already imports from), not a mock of the enforcement logic.
"""
from __future__ import annotations

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
import pytest
import yaml

pytest.importorskip("mcp")

from examples.mcp_gateway_demo import configuration, lease_data


async def _direct_call(upstream_url: str, secret: str, tool: str, arguments: dict) -> dict:
    """The bypass half: talk to the mock upstream directly, with its own
    credential, never mentioning Agent Plane at all."""
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + secret}, trust_env=False) as http:
        async with Client(streamable_http_client(upstream_url, http_client=http), mode="2026-07-28", cache=None) as client:
            reply = await client.call_tool(tool, arguments)
            return {"is_error": reply.is_error,
                    "content": [c.model_dump(mode="json", by_alias=True) for c in reply.content]}


@pytest.fixture()
def demo_servers(tmp_path):
    upstream_port, gateway_port = 8791, 8790
    (tmp_path / "gateway.yaml").write_text(yaml.safe_dump(configuration(upstream_port)), encoding="utf-8")
    (tmp_path / "leases.yaml").write_text(yaml.safe_dump({"leases": [lease_data()]}), encoding="utf-8")
    env = os.environ.copy()
    env.update({"ENVIRONMENT": "development", "STORAGE_BACKEND": "local", "IDENTITY_MODE": "jwt_claims",
                "SQLITE_PATH": str(tmp_path / "audit.db"), "LEASES_FILE": str(tmp_path / "leases.yaml"),
                "MCP_GATEWAY_FILE": str(tmp_path / "gateway.yaml"), "JWT_SECRET": secrets.token_urlsafe(32),
                "ADMIN_TOKEN": secrets.token_urlsafe(32), "AUDIT_SIGNING_KEY": secrets.token_urlsafe(32),
                "MOCK_UPSTREAM_SECRET": secrets.token_urlsafe(32)})
    demo_script = str(Path("examples/mcp_gateway_demo.py").resolve())
    processes = [
        subprocess.Popen([sys.executable, demo_script, "--mock-upstream", "--upstream-port", str(upstream_port)],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT),
        subprocess.Popen([sys.executable, "-m", "agent_plane.cli", "serve", "--host", "127.0.0.1", "--port", str(gateway_port)],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT),
    ]
    gateway_url = f"http://127.0.0.1:{gateway_port}"
    upstream_url = f"http://127.0.0.1:{upstream_port}/mcp"
    try:
        with httpx.Client(timeout=2, trust_env=False) as http:
            for _ in range(100):
                if any(p.poll() is not None for p in processes):
                    out = b"".join(p.stdout.read() or b"" for p in processes if p.stdout)
                    raise RuntimeError(f"Demo process stopped early: {out.decode(errors='replace')[:2000]}")
                try:
                    ready = http.get(gateway_url + "/readyz").status_code == 200
                    upstream_ready = http.get(f"http://127.0.0.1:{upstream_port}/stats",
                        headers={"Authorization": "Bearer " + env["MOCK_UPSTREAM_SECRET"]}).status_code == 200
                    if ready and upstream_ready:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError("Demo servers did not start in time")
        yield gateway_url, upstream_url, env
    finally:
        for p in processes:
            p.terminate()
        for p in processes:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()


def test_denied_action_is_blocked_through_the_gateway_but_succeeds_direct(demo_servers):
    """The core Phase 30 comparison: the SAME action, through the two
    paths, with opposite outcomes - because only one of them is actually
    governed."""
    gateway_url, upstream_url, env = demo_servers
    token = jwt.encode({"sub": "demo-operator", "tenant": "acme", "agent_id": "repo-agent",
                        "allowed_tools": ["branch", "repo.branches", "repo.delete_branch"],
                        "exp": datetime.now(UTC) + timedelta(hours=1)}, env["JWT_SECRET"], algorithm="HS256")

    # Governed path: deleting the protected "main" branch is refused, and
    # never reaches the upstream (agent_plane's own evidence says so).
    from examples.mcp_gateway_demo import exercise
    governed = asyncio.run(exercise(gateway_url, token))
    protected = next(r for r in governed if r["arguments"] == {"branch": "main"})
    assert protected["is_error"] is True
    assert protected["evidence"]["execution_status"] == "not_dispatched"

    # Bypass path: the exact same upstream operation, called directly with
    # the upstream's own credential - Agent Plane is never in this call at
    # all, so nothing it decided can apply. This must succeed, proving the
    # path is uncontrolled unless the credential itself is kept out of the
    # caller's hands (agent_plane/gateway/mcp.py's own design principle).
    direct = asyncio.run(_direct_call(upstream_url, env["MOCK_UPSTREAM_SECRET"], "delete_branch", {"branch": "main"}))
    assert direct["is_error"] is False

    with httpx.Client(timeout=2, trust_env=False) as http:
        stats = http.get(upstream_url.replace("/mcp", "/stats"),
                         headers={"Authorization": "Bearer " + env["MOCK_UPSTREAM_SECRET"]}).json()
    # The protected branch delete landed on the upstream exactly once - via
    # the bypass path, never via the gateway.
    assert stats["calls"].count("delete_branch") == 1


if __name__ == "__main__":
    pytest.main([__file__, "-q", "-s"])
