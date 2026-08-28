"""Live smoke test for a running agent-plane deployment - every edge, one call
each. Not a substitute for `pytest` (that's the real test suite); this is what
you run *after* deploying to confirm the wiring is live end to end.

    JWT_SECRET=<yours> python examples/verify_deployment.py http://localhost:8000
    # ADMIN_TOKEN=<yours> too, to also check the admin-gated checks

Exits non-zero if anything unexpected happens.
"""
from __future__ import annotations

import os
import sys

import httpx
import jwt

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")

failures: list[str] = []


def token(**claims) -> str:
    return jwt.encode({"sub": "smoke-test", "tenant": "default", **claims}, JWT_SECRET, algorithm="HS256")


def auth(**claims) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(**claims)}"}


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=10.0)

    r = c.get("/healthz")
    check("healthz", r.status_code == 200, r.text)

    r = c.get("/readyz")
    check("readyz", r.status_code == 200, r.text)

    # Model edge: policy denies confidential finance data to an external model
    # *before* any upstream call - no API key needed to prove this.
    r = c.post("/v1/chat/completions", headers=auth(department="finance"), json={
        "model": "gpt-4.1", "data_classification": "confidential",
        "messages": [{"role": "user", "content": "Q3 figures"}],
    })
    check("model edge: confidential finance -> external model denied", r.status_code == 403, r.text)

    # Tool broker: allowed, denied-by-allowlist, approval-required.
    r = c.post("/v1/tools/invoke", headers=auth(), json={"tool": "search_kb", "arguments": {"q": "reset"}})
    check("tool edge: catalogued tool executes", r.status_code == 200, r.text)

    r = c.post("/v1/tools/invoke", headers=auth(agent_id="a1", allowed_tools=["search_kb"]),
               json={"tool": "echo", "arguments": {}})
    check("tool edge: outside allow-list denied", r.status_code == 403, r.text)

    r = c.post("/v1/tools/invoke", headers=auth(), json={"tool": "wire_transfer", "arguments": {"amount": 1}})
    check("tool edge: sensitive tool requires approval", r.status_code == 202, r.text)

    # RAG edge: relevance is not permission - a document outside clearance is filtered.
    r = c.post("/v1/retrieve", headers=auth(department="support"), json={"source": "kb", "query": "reset"})
    check("rag edge: authorized retrieval", r.status_code == 200, r.text)
    if r.status_code == 200:
        body = r.json()
        check("rag edge: x_control_plane reports filtering", "filtered_by_authorization" in body["x_control_plane"])

    # Task-authority edge: capability != authority (needs config/leases.yaml's demo leases).
    r = c.post("/v1/authorize", headers=auth(agent_id="devops-agent"), json={
        "task": "fix-staging-checkout", "action": "deployment.restart", "resource": "staging/checkout",
    })
    check("authority edge: in-scope action allowed", r.status_code == 200, r.text)

    r = c.post("/v1/authorize", headers=auth(agent_id="devops-agent"), json={
        "task": "fix-staging-checkout", "action": "deployment.delete", "resource": "production/checkout",
    })
    check("authority edge: out-of-scope resource denied", r.status_code == 403, r.text)

    # A2A edge: disabled (404) unless IDENTITY_MODE=delegation is configured.
    r = c.post("/v1/agents/delegate", headers=auth(agent_id="parent"),
               json={"agent": "child", "scope": {"tools": []}})
    check("a2a edge: reachable (200/403) or cleanly disabled (404)", r.status_code in (200, 403, 404), r.text)

    # Usage metering.
    r = c.get("/v1/usage", headers=auth())
    check("usage edge", r.status_code == 200, r.text)

    if ADMIN_TOKEN:
        h = {"X-Admin-Token": ADMIN_TOKEN}
        r = c.get("/v1/audit?limit=5", headers=h)
        check("admin: audit chain readable", r.status_code == 200, r.text)
        if r.status_code == 200:
            events = r.json()["events"]
            check("admin: audit events are signed", bool(events) and all(e["signature"] for e in events))

        r = c.get("/admin/policies", headers=h)
        check("admin: policy bundle inspectable", r.status_code == 200, r.text)
    else:
        print("[SKIP] admin-gated checks - set ADMIN_TOKEN to include them")

    print(f"\n{len(failures)} failure(s)." if failures else "\nAll checks passed.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
