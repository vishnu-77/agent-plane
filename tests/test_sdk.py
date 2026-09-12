"""Client contracts that must never turn an invalid/pending reply into ALLOW."""
from __future__ import annotations

import httpx
import pytest
from agentplane import AgentPlane, AuthorizationProtocolError


@pytest.fixture
def plane(monkeypatch):
    original_client = httpx.Client

    def make(handler):
        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(
            httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs)
        )
        return AgentPlane("https://runtime.example", "agent-token")

    return make


@pytest.mark.parametrize("status,decision", [(200, "allow"), (202, "approval_required"), (403, "deny")])
@pytest.mark.parametrize("wrapped", [False, True])
def test_decision_statuses(plane, status, decision, wrapped):
    payload = {"decision": decision, "reason": "TEST_REASON", "lease": "lease-1", "evidence_id": "az_1"}

    def handler(request):
        assert request.url.path == "/v1/authorize"
        assert request.headers["Authorization"] == "Bearer agent-token"
        return httpx.Response(status, json={"detail": payload} if wrapped else payload)

    with plane(handler) as client:
        result = client.authorize(task="task", action="branch.delete", resource="repo/main")
    assert result.decision == decision
    assert result.allowed is (decision == "allow")
    assert result.evidence_id == "az_1"


@pytest.mark.parametrize("status,payload", [
    (200, None), (200, []), (200, {}), (200, {"detail": "unexpected"}),
    (200, {"decision": "allow", "reason": "OK"}),
    (202, {"decision": "allow", "reason": "OK", "evidence_id": "az_1"}),
    (403, {"decision": "allow", "reason": "OK", "evidence_id": "az_1"}),
    (200, {"decision": "allow", "reason": "", "evidence_id": "az_1"}),
    (200, {"decision": "allow", "reason": "OK", "evidence_id": ""}),
    (200, {"decision": "allow", "reason": "OK", "evidence_id": 1}),
    (200, {"decision": "allow", "reason": "OK", "evidence_id": "az_1", "lease": {}}),
    (204, {}),
])
def test_inconsistent_response_fails_closed(plane, status, payload):
    with plane(lambda request: httpx.Response(status, json=payload)) as client:
        with pytest.raises(AuthorizationProtocolError):
            client.authorize(task="task", action="branch.delete", resource="repo/main")


def test_non_json_fails_closed(plane):
    with plane(lambda request: httpx.Response(200, text="upstream unavailable")) as client:
        with pytest.raises(AuthorizationProtocolError):
            client.authorize(task="task", action="branch.delete", resource="repo/main")


@pytest.mark.parametrize("status", [401, 404, 429, 500, 503])
def test_http_errors_propagate(plane, status):
    with plane(lambda request: httpx.Response(status, json={"detail": "error"})) as client:
        with pytest.raises(httpx.HTTPStatusError):
            client.authorize(task="task", action="branch.delete", resource="repo/main")


def test_transport_failure_is_not_retried(plane):
    requests = []

    def unavailable(request):
        requests.append(request)
        raise httpx.ConnectError("unavailable", request=request)

    with plane(unavailable) as client:
        with pytest.raises(httpx.ConnectError):
            client.authorize(task="task", action="branch.delete", resource="repo/main")
    assert len(requests) == 1
