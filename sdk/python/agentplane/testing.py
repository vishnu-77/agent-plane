"""Executor conformance kit.

The one property an agent-plane integration must have: **the real action runs
only after an explicit ALLOW.** Everything else - DENY, APPROVAL_REQUIRED,
a 500, a malformed body, a dropped connection, a 200 with the wrong shape -
must leave the target system untouched.

This module runs your executor against every one of those cases using an
in-process mock of the agent-plane HTTP API (no server needed) and reports
which cases executed when they must not have.

    from agentplane import AgentPlane
    from agentplane.testing import check_executor

    def build(plane: AgentPlane, execute):
        # Return a zero-arg callable that performs ONE governed action
        # through `plane` and calls `execute()` only when allowed.
        def run():
            d = plane.authorize(task="t", action="deployment.restart", resource="staging/x")
            if d.allowed:
                execute()
        return run

    def test_my_executor_fails_closed():
        check_executor(build)          # raises ConformanceFailure with details

Use :func:`mock_plane` on its own to drive a client with canned responses.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx

from agentplane import AgentPlane

__all__ = ["Case", "ConformanceFailure", "ConformanceReport", "check_executor", "mock_plane", "CASES"]

Executor = Callable[[], Any]
Builder = Callable[[AgentPlane, Callable[[], None]], Executor]


@dataclass(frozen=True)
class Case:
    name: str
    must_execute: bool
    status: int | None = None            # None = transport failure
    body: Any = None                       # dict -> JSON; str -> raw text
    wrapped: bool = False                  # wrap dict body in {"detail": ...}
    description: str = ""


_OK = {"decision": "allow", "reason": "ACTION_WITHIN_TASK_AUTHORITY", "lease": "lease-1", "evidence_id": "az_ok"}

CASES: tuple[Case, ...] = (
    Case("allow", True, 200, _OK, description="A clean ALLOW must execute exactly once."),
    Case("deny", False, 403, {**_OK, "decision": "deny", "reason": "RESOURCE_PROTECTED"}, wrapped=True),
    Case("approval_required", False, 202,
         {**_OK, "decision": "approval_required", "reason": "ACTION_REQUIRES_APPROVAL", "approval_id": "apr_1"},
         wrapped=True, description="A pending approval is not permission."),
    Case("quarantine", False, 423, {**_OK, "decision": "quarantine", "reason": "AGENT_QUARANTINED"}, wrapped=True,
         description="A quarantined agent is held; nothing runs."),
    Case("http_200_simulate_claims_enforced", False, 200,
         {**_OK, "decision": "simulate", "reason": "NO_ACTIVE_LEASE", "enforced": True},
         description="A simulate that claims to be enforced is inconsistent and must be refused."),
    Case("http_500", False, 500, {"error": "internal_error"}),
    Case("http_401", False, 401, {"detail": "invalid token"}),
    Case("http_404", False, 404, {"detail": "not found"}),
    Case("http_429", False, 429, {"error": "rate_limited"}),
    Case("http_200_wrong_decision", False, 200, {**_OK, "decision": "deny"},
         description="Status and decision disagree; must not be trusted."),
    Case("http_200_no_evidence", False, 200, {"decision": "allow", "reason": "OK"}),
    Case("http_200_empty_object", False, 200, {}),
    Case("http_200_list", False, 200, []),
    Case("http_200_not_json", False, 200, "<html>maintenance</html>"),
    Case("http_204_no_content", False, 204, None),
    Case("transport_failure", False, None, None, description="Connection dropped before any reply."),
)


@dataclass
class ConformanceReport:
    passed: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failed

    def __str__(self) -> str:
        lines = [f"agent-plane executor conformance: {len(self.passed)} passed, {len(self.failed)} failed"]
        for name, why in self.failed.items():
            lines.append(f"  FAIL {name}: {why}")
        return "\n".join(lines)


class ConformanceFailure(AssertionError):
    def __init__(self, report: ConformanceReport):
        super().__init__(str(report))
        self.report = report


def _response_for(case: Case) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if case.status is None:
            raise httpx.ConnectError("connection reset by peer", request=request)
        if case.body is None:
            return httpx.Response(case.status)
        if isinstance(case.body, str):
            return httpx.Response(case.status, text=case.body)
        body = {"detail": case.body} if case.wrapped else case.body
        return httpx.Response(case.status, content=json.dumps(body).encode(),
                              headers={"content-type": "application/json"})
    return handler


def _recorder(calls: list[int]) -> Callable[[], None]:
    def execute() -> None:
        calls.append(1)
    return execute


def mock_plane(handler: Callable[[httpx.Request], httpx.Response], *, token: str = "agent-token",
               base_url: str = "https://agent-plane.test") -> AgentPlane:
    """An :class:`AgentPlane` whose HTTP calls are answered by ``handler``."""
    return AgentPlane(base_url, token, transport=httpx.MockTransport(handler))


def check_executor(build: Builder, *, cases: Iterable[Case] = CASES,
                   raise_on_failure: bool = True) -> ConformanceReport:
    """Run ``build(plane, execute)()`` once per case and verify fail-closed.

    For each case the kit constructs a mock-backed client, calls your builder
    to get a runnable executor, runs it, and checks whether ``execute`` was
    called. Exceptions raised by the executor are expected for every
    non-ALLOW case and are swallowed; what matters is whether ``execute`` ran.
    """
    report = ConformanceReport()
    for case in cases:
        calls: list[int] = []
        execute = _recorder(calls)
        plane = mock_plane(_response_for(case))
        try:
            executor = build(plane, execute)
            try:
                executor()
                raised = None
            except Exception as exc:  # noqa: BLE001 - expected for non-ALLOW cases
                raised = exc
        finally:
            plane.close()

        if case.must_execute:
            if len(calls) == 1 and raised is None:
                report.passed.append(case.name)
            elif len(calls) != 1:
                report.failed[case.name] = f"expected exactly one execution on ALLOW, got {len(calls)}"
            else:
                report.failed[case.name] = f"raised on a valid ALLOW: {raised!r}"
        else:
            if calls:
                report.failed[case.name] = (
                    f"executed {len(calls)}x without an ALLOW ({case.description or case.name})")
            else:
                report.passed.append(case.name)
    if raise_on_failure and not report.ok:
        raise ConformanceFailure(report)
    return report
