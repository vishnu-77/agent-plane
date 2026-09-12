"""The SDK path a developer follows: an API key, a task, and a decision."""
from __future__ import annotations

import httpx
import pytest
from agentplane import AgentPlane
from agentplane.adapters import NotAuthorized, govern
from fastapi.testclient import TestClient

from tests.test_accounts import make_key, make_project, signup


class _AppTransport(httpx.BaseTransport):
    """Route SDK calls through the ASGI app, so this is end to end without a socket."""

    def __init__(self, client: TestClient):
        self._client = client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._client.request(request.method, str(request.url.raw_path.decode()),
                                        headers=dict(request.headers), content=request.content)
        return httpx.Response(response.status_code, headers=response.headers, content=response.content)


@pytest.fixture()
def plane(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("JWT_SECRET", "sdk-project-secret-0123")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("AGENTPLANE_API_KEY", raising=False)
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as client:
        signup(client)
        project = make_project(client)
        secret = make_key(client, project["id"])["secret"]
        sdk = AgentPlane(api_key=secret, url="http://plane", agent="worker",
                         transport=_AppTransport(client))
        yield sdk, client, project, secret
        sdk.close()
    get_settings.cache_clear()


def test_a_key_is_the_only_credential_needed(monkeypatch):
    monkeypatch.setenv("AGENTPLANE_API_KEY", "ap_live_x000000000000000")
    monkeypatch.setenv("AGENTPLANE_URL", "http://from-env")
    sdk = AgentPlane()
    assert sdk._client.base_url == httpx.URL("http://from-env")
    sdk.close()
    monkeypatch.delenv("AGENTPLANE_API_KEY")
    with pytest.raises(ValueError, match="AGENTPLANE_API_KEY"):
        AgentPlane()


def test_task_handle_authorizes_and_reports(plane):
    sdk, client, project, _ = plane
    client.patch(f"/v1/projects/{project['id']}", json={"mode": "enforce"})
    client.post("/v1/rules", json={"project": project["id"], "name": "coding",
                                   "allow": ["filesystem.read", "filesystem.write"],
                                   "ask": ["git.push"], "never": ["repository.delete"],
                                   "resources": ["workspace/*", "github://*"]})

    with sdk.task("fix-authentication-tests",
                  origin={"kind": "prompt", "ref": "p1", "created_by": "human:dev"}) as task:
        allowed = task.authorize("filesystem.write", "workspace/src/auth.ts")
        assert allowed.allowed and allowed.proceed
        assert allowed.consequence["environment"] == "workspace"
        assert allowed.explanation

        asked = task.authorize("git.push", "github://acme/app")
        assert asked.needs_approval and asked.approval_id

        refused = task.authorize("repository.delete", "github://acme/app")
        assert not refused.proceed and refused.reason == "ACTION_REFUSED_BY_RULE"

        # Reporting a raw tool call normalizes on the server.
        reported = task.report(tool="Edit", arguments={"file_path": "src/login.ts"})
        assert reported["action"] == "filesystem.write"
        assert reported["resource"] == "workspace/src/login.ts"

    tasks = client.get(f"/v1/tasks?project={project['id']}").json()["tasks"]
    assert tasks[0]["id"] == "fix-authentication-tests"
    assert tasks[0]["origin"]["created_by"] == "human:dev"
    agents = client.get(f"/v1/agents?project={project['id']}").json()["agents"]
    assert agents[0]["id"] == "worker"


def test_observe_mode_lets_the_adapter_proceed(plane):
    sdk, client, project, _ = plane
    assert client.get(f"/v1/projects/{project['id']}").json()["project"]["mode"] == "observe"
    ran: list[str] = []

    @govern(sdk, task="t", action="repository.delete", resource="github://{repo}")
    def delete_repo(repo: str) -> None:
        ran.append(repo)

    # Observe blocks nothing, so the wrapped function still runs...
    delete_repo("acme/app")
    assert ran == ["acme/app"]
    # ... and the attempt is on record as something enforcement would refuse.
    decisions = client.get(f"/v1/decisions?project={project['id']}").json()["decisions"]
    assert decisions[0]["outcome"] == "simulate" and decisions[0]["would_be"] == "deny"

    client.patch(f"/v1/projects/{project['id']}", json={"mode": "enforce"})
    with pytest.raises(NotAuthorized):
        delete_repo("acme/app")
    assert ran == ["acme/app"]
