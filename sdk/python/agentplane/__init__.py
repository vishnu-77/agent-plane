"""Python client for agent-plane.

Two clients, matching the two credential roles:

* :class:`AgentPlane` - used by the trusted executor acting for one agent,
  authenticated with the agent bearer token. ``authorize()`` is the whole
  contract: before executing a proposed action against a real system, ask
  whether it is authorised for the current task, and execute only on
  ``decision.allowed``. It also resumes approvals and delegates leases.
* :class:`AgentPlaneAdmin` - used by the trusted backend / operator tooling
  with ``ADMIN_TOKEN``. Issues, shrinks, and revokes leases, works the
  approval queue, and reads audit evidence. Never hand this token to an agent.

    from agentplane import AgentPlane

    plane = AgentPlane("http://localhost:8000", token)
    decision = plane.authorize(task="fix-staging", action="deployment.delete",
                                resource="production/checkout")
    if decision.allowed:
        do_the_thing()
    elif decision.needs_approval:
        decision = plane.wait_for_approval(decision, timeout=600)
        ...

Framework adapters live in :mod:`agentplane.adapters`; the executor
conformance kit in :mod:`agentplane.testing`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

__all__ = [
    "AgentPlane",
    "AgentPlaneAdmin",
    "ApprovalRequest",
    "ApprovalTimeout",
    "AuthorityDecision",
    "AuthorizationProtocolError",
    "Lease",
]


class AuthorizationProtocolError(RuntimeError):
    """The server did not return a consistent, usable authorization decision.

    Callers must stop execution on this error, just as on an HTTP or transport
    error. An unexpected response must never authorize a real action.
    """


class ApprovalTimeout(TimeoutError):
    """``wait_for_approval`` gave up before the request was decided."""


_EXPECTED = {200: "allow", 202: "approval_required", 403: "deny"}


@dataclass(frozen=True)
class AuthorityDecision:
    decision: str          # "allow" | "deny" | "approval_required"
    reason: str             # machine-readable reason code, e.g. RESOURCE_PROTECTED
    lease: str | None
    evidence_id: str        # id of the signed audit record backing this decision
    approval_id: str | None = None   # set on approval_required (and on resumed decisions)
    context: dict[str, str] = field(default_factory=dict)
    task: str = ""
    action: str = ""
    resource: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    @property
    def needs_approval(self) -> bool:
        return self.decision == "approval_required"


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    status: str            # pending | approved | rejected | consumed | expired
    tenant: str
    subject: str
    task: str
    action: str
    resource: str
    lease_id: str | None
    evidence_id: str
    created_at: str
    expires_at: str | None = None
    decided_at: str | None = None
    decided_by: str | None = None
    note: str | None = None
    context: dict[str, str] = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.status == "pending"

    @classmethod
    def from_json(cls, body: Any) -> ApprovalRequest:
        if not isinstance(body, dict) or not isinstance(body.get("id"), str) \
                or not isinstance(body.get("status"), str):
            raise AuthorizationProtocolError("Invalid approval payload")
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in body.items() if k in known})


@dataclass(frozen=True)
class Lease:
    id: str
    task: str
    subject: str
    tenant: str
    resources: list[str]
    actions: list[str]
    protected_resources: list[str]
    max_uses: dict[str, int]
    require_approval: list[str]
    expires_at: str | None
    maximum_impact: str
    child_authority: str
    revoked: bool

    @classmethod
    def from_json(cls, body: Any) -> Lease:
        if not isinstance(body, dict) or not isinstance(body.get("id"), str):
            raise AuthorizationProtocolError("Invalid lease payload")
        return cls(
            id=body["id"], task=body.get("task", ""), subject=body.get("subject", ""),
            tenant=body.get("tenant", "default"),
            resources=list(body.get("resources") or []), actions=list(body.get("actions") or []),
            protected_resources=list(body.get("protected_resources") or []),
            max_uses=dict(body.get("max_uses") or {}),
            require_approval=list(body.get("require_approval") or []),
            expires_at=body.get("expires_at"), maximum_impact=body.get("maximum_impact", ""),
            child_authority=body.get("child_authority", ""), revoked=bool(body.get("revoked")),
        )


def _json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError as exc:
        raise AuthorizationProtocolError("Invalid JSON from agent-plane") from exc


def _parse_decision(resp: httpx.Response, *, task: str, action: str, resource: str) -> AuthorityDecision:
    if resp.status_code not in _EXPECTED:
        resp.raise_for_status()
        raise AuthorizationProtocolError("Unexpected authorization HTTP status")
    # FastAPI wraps 202/403 in "detail"; HTTP success is not permission.
    body = _json(resp)
    payload = body.get("detail", body) if isinstance(body, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("decision") != _EXPECTED[resp.status_code]
        or not isinstance(payload.get("reason"), str)
        or not payload["reason"]
        or not isinstance(payload.get("evidence_id"), str)
        or not payload["evidence_id"]
        or (payload.get("lease") is not None and not isinstance(payload["lease"], str))
        or (payload.get("approval_id") is not None and not isinstance(payload["approval_id"], str))
    ):
        raise AuthorizationProtocolError("Inconsistent authorization decision")
    if resp.status_code == 202 and not payload.get("approval_id"):
        # Older servers (< 0.4) return no approval id; treat as a plain pause.
        approval_id = None
    else:
        approval_id = payload.get("approval_id")
    context = payload.get("context") or {}
    if not isinstance(context, dict):
        raise AuthorizationProtocolError("Inconsistent authorization context")
    return AuthorityDecision(
        decision=payload["decision"], reason=payload["reason"], lease=payload.get("lease"),
        evidence_id=payload["evidence_id"], approval_id=approval_id,
        context={str(k): str(v) for k, v in context.items()},
        task=task, action=action, resource=resource,
    )


def _make_client(base_url: str, headers: dict[str, str], timeout: float,
                 transport: httpx.BaseTransport | None) -> httpx.Client:
    kwargs: dict[str, Any] = {"base_url": base_url.rstrip("/"), "headers": headers, "timeout": timeout}
    if transport is not None:
        kwargs["transport"] = transport
    return httpx.Client(**kwargs)


class AgentPlane:
    """Executor-side client, authenticated with the agent bearer token."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 10.0,
                 transport: httpx.BaseTransport | None = None):
        self._client = _make_client(base_url, {"Authorization": f"Bearer {token}"}, timeout, transport)

    # -- authorization ---------------------------------------------------------- #
    def authorize(self, *, task: str, action: str, resource: str,
                  approval: str | None = None,
                  context: dict[str, str] | None = None) -> AuthorityDecision:
        """Ask whether ``action`` on ``resource`` is authorised for ``task``.

        ``approval`` resumes a previously raised approval request; ``context``
        attaches provenance (parent evidence id, conversation id, ...) to the
        audit record. Neither changes what you must do with the result:
        execute only when ``decision.allowed`` is true.
        """
        body: dict[str, Any] = {"task": task, "action": action, "resource": resource}
        if approval:
            body["approval"] = approval
        if context:
            body["context"] = dict(context)
        resp = self._client.post("/v1/authorize", json=body)
        return _parse_decision(resp, task=task, action=action, resource=resource)

    # -- approvals ---------------------------------------------------------------- #
    def get_approval(self, approval_id: str) -> ApprovalRequest:
        resp = self._client.get(f"/v1/approvals/{approval_id}")
        resp.raise_for_status()
        return ApprovalRequest.from_json(_json(resp))

    def wait_for_approval(self, decision: AuthorityDecision, *, timeout: float = 300.0,
                          interval: float = 2.0) -> AuthorityDecision:
        """Poll an approval_required decision until it is decided, then resume.

        Returns the resumed decision (ALLOW / ACTION_APPROVED on approval,
        DENY otherwise). Raises :class:`ApprovalTimeout` if nothing happens
        within ``timeout`` seconds; the request stays open on the server.
        """
        if not decision.needs_approval or not decision.approval_id:
            return decision
        deadline = time.monotonic() + timeout
        while True:
            req = self.get_approval(decision.approval_id)
            if req.status != "pending":
                return self.authorize(task=decision.task, action=decision.action,
                                      resource=decision.resource, approval=decision.approval_id,
                                      context=decision.context or None)
            if time.monotonic() >= deadline:
                raise ApprovalTimeout(f"approval {decision.approval_id} still pending after {timeout}s")
            time.sleep(min(interval, max(0.0, deadline - time.monotonic())))

    # -- delegation --------------------------------------------------------------- #
    def delegate(self, lease_id: str, *, agent: str, **narrowing: Any) -> Lease:
        """Mint an attenuated child lease for ``agent`` (lease-holder self-service)."""
        resp = self._client.post(f"/v1/leases/{lease_id}/delegate", json={"agent": agent, **narrowing})
        resp.raise_for_status()
        body = _json(resp)
        return Lease.from_json(body.get("lease") if isinstance(body, dict) else None)

    # -- lifecycle ------------------------------------------------------------------ #
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> AgentPlane:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class AgentPlaneAdmin:
    """Backend / operator client, authenticated with ``ADMIN_TOKEN``."""

    def __init__(self, base_url: str, admin_token: str, *, timeout: float = 10.0,
                 transport: httpx.BaseTransport | None = None):
        self._client = _make_client(base_url, {"X-Admin-Token": admin_token}, timeout, transport)

    # -- leases ------------------------------------------------------------------- #
    def issue_lease(self, *, id: str, subject: str, task: str, resources: list[str],
                    actions: list[str], tenant: str = "default",
                    protected_resources: list[str] | None = None,
                    require_approval: list[str] | None = None, max_uses: dict[str, int] | None = None,
                    expires_at: str | None = None, maximum_impact: str = "reversible",
                    child_authority: str = "none") -> Lease:
        body = {
            "id": id, "subject": subject, "task": task, "tenant": tenant,
            "resources": resources, "actions": actions,
            "protected_resources": protected_resources or [], "require_approval": require_approval or [],
            "max_uses": max_uses or {}, "expires_at": expires_at, "maximum_impact": maximum_impact,
            "child_authority": child_authority,
        }
        resp = self._client.post("/v1/leases", json=body)
        resp.raise_for_status()
        return Lease.from_json(_json(resp).get("lease"))

    def issue_from_template(self, template: str, *, subject: str, task: str,
                            tenant: str = "default", variables: dict[str, str] | None = None,
                            id: str | None = None) -> Lease:
        body: dict[str, Any] = {"template": template, "subject": subject, "task": task,
                                "tenant": tenant, "variables": variables or {}}
        if id:
            body["id"] = id
        resp = self._client.post("/v1/leases/from-template", json=body)
        resp.raise_for_status()
        return Lease.from_json(_json(resp).get("lease"))

    def list_templates(self) -> list[dict[str, Any]]:
        resp = self._client.get("/v1/lease-templates")
        resp.raise_for_status()
        return list(_json(resp).get("templates") or [])

    def get_lease(self, lease_id: str) -> Lease:
        resp = self._client.get(f"/v1/leases/{lease_id}")
        resp.raise_for_status()
        return Lease.from_json(_json(resp))

    def shrink_lease(self, lease_id: str, **narrower: Any) -> Lease:
        resp = self._client.patch(f"/v1/leases/{lease_id}", json=narrower)
        resp.raise_for_status()
        return Lease.from_json(_json(resp).get("lease"))

    def revoke_lease(self, lease_id: str) -> None:
        resp = self._client.delete(f"/v1/leases/{lease_id}")
        resp.raise_for_status()

    # -- approvals ------------------------------------------------------------------ #
    def list_approvals(self, status: str = "pending", *, tenant: str | None = None,
                       limit: int = 100) -> list[ApprovalRequest]:
        params: dict[str, Any] = {"status": status, "limit": limit}
        if tenant:
            params["tenant"] = tenant
        resp = self._client.get("/v1/approvals", params=params)
        resp.raise_for_status()
        return [ApprovalRequest.from_json(item) for item in _json(resp).get("approvals") or []]

    def get_approval(self, approval_id: str) -> ApprovalRequest:
        resp = self._client.get(f"/v1/approvals/{approval_id}")
        resp.raise_for_status()
        return ApprovalRequest.from_json(_json(resp))

    def approve(self, approval_id: str, *, note: str | None = None,
                decided_by: str | None = None) -> ApprovalRequest:
        return self._decide(approval_id, "approve", note, decided_by)

    def reject(self, approval_id: str, *, note: str | None = None,
               decided_by: str | None = None) -> ApprovalRequest:
        return self._decide(approval_id, "reject", note, decided_by)

    def _decide(self, approval_id: str, verb: str, note: str | None,
                decided_by: str | None) -> ApprovalRequest:
        body: dict[str, Any] = {}
        if note is not None:
            body["note"] = note
        if decided_by is not None:
            body["decided_by"] = decided_by
        resp = self._client.post(f"/v1/approvals/{approval_id}/{verb}", json=body)
        resp.raise_for_status()
        return ApprovalRequest.from_json(_json(resp).get("approval"))

    # -- evidence -------------------------------------------------------------------- #
    def audit(self, *, limit: int = 50) -> list[dict[str, Any]]:
        resp = self._client.get("/v1/audit", params={"limit": limit})
        resp.raise_for_status()
        body = _json(resp)
        return list(body.get("events") if isinstance(body, dict) else body or [])

    # -- lifecycle ------------------------------------------------------------------- #
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> AgentPlaneAdmin:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
