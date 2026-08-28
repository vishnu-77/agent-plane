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

__all__ = ["AgentPlane", "AuthorityDecision"]


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
        # 200 (allow), 202 (approval_required), and 403 (deny) all carry the same
        # decision body - FastAPI wraps non-2xx ones in {"detail": ...}.
        body = resp.json()
        payload = body.get("detail", body) if resp.status_code >= 300 else body
        return AuthorityDecision(
            decision=payload.get("decision", "deny"),
            reason=payload.get("reason", "UNKNOWN"),
            lease=payload.get("lease"),
            evidence_id=payload.get("evidence_id", ""),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AgentPlane":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
