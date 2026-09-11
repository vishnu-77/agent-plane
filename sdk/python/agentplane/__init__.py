"""Minimal HTTP client for agent-plane's `authorize()` primitive.

The whole contract is one call: before executing a proposed action against a
real system, ask whether it's authorised for the current task.

    from agentplane import AgentPlane

    plane = AgentPlane("http://localhost:8000", token)
    decision = plane.authorize(task="fix-staging", action="deployment.delete",
                                resource="production/checkout")
    if decision.allowed:
        do_the_thing()
    else:
        print(decision.reason)  # e.g. RESOURCE_OUTSIDE_DELEGATED_SCOPE
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

__all__ = ["AgentPlane", "AuthorityDecision", "AuthorizationProtocolError"]


class AuthorizationProtocolError(RuntimeError):
    """The server did not return a consistent, usable authorization decision.

    Callers must stop execution on this error, just as on an HTTP or transport
    error. An unexpected response must never authorize a real action.
    """


_EXPECTED = {200: "allow", 202: "approval_required", 403: "deny"}


@dataclass(frozen=True)
class AuthorityDecision:
    decision: str          # "allow" | "deny" | "approval_required"
    reason: str             # machine-readable reason code, e.g. RESOURCE_PROTECTED
    lease: str | None
    evidence_id: str        # id of the signed audit record backing this decision

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


class AgentPlane:
    def __init__(self, base_url: str, token: str, *, timeout: float = 10.0):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    def authorize(self, *, task: str, action: str, resource: str) -> AuthorityDecision:
        resp = self._client.post(
            "/v1/authorize", json={"task": task, "action": action, "resource": resource}
        )
        if resp.status_code not in _EXPECTED:
            resp.raise_for_status()
            raise AuthorizationProtocolError("Unexpected authorization HTTP status")
        # FastAPI also wraps HTTP 202 in detail; HTTP success is not permission.
        try:
            body = resp.json()
        except ValueError as exc:
            raise AuthorizationProtocolError("Invalid authorization JSON") from exc
        payload = body.get("detail", body) if isinstance(body, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("decision") != _EXPECTED[resp.status_code]
            or not isinstance(payload.get("reason"), str)
            or not payload["reason"]
            or not isinstance(payload.get("evidence_id"), str)
            or not payload["evidence_id"]
            or (payload.get("lease") is not None and not isinstance(payload["lease"], str))
        ):
            raise AuthorizationProtocolError("Inconsistent authorization decision")
        return AuthorityDecision(
            decision=payload["decision"],
            reason=payload["reason"],
            lease=payload.get("lease"),
            evidence_id=payload["evidence_id"],
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> AgentPlane:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
