"""Exercise a disposable runtime through real HTTP and the installed Python SDK.

Creates one short-lived lease and four audit events; never executes a real tool.
Use this only against a local/test runtime. Reads ADMIN_TOKEN and JWT_SECRET.
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt
from agentplane import AgentPlane


def wait_ready(base_url: str, timeout: float = 45) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=1.0, trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                if client.get(f"{base_url}/readyz").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
    raise RuntimeError("Test runtime did not become ready")


def exercise_runtime(base_url: str, admin: str, secret: str) -> dict:
    wait_ready(base_url)
    identity = f"smoke:{uuid4().hex}"
    expiry = datetime.now(UTC) + timedelta(minutes=5)
    token = jwt.encode(
        {"sub": "smoke-user", "agent_id": identity, "tenant": "smoke",
         "allowed_tools": ["deployment"], "exp": expiry},
        secret, algorithm="HS256",
    )
    admin_headers = {"X-Admin-Token": admin}
    with httpx.Client(base_url=base_url, timeout=5, trust_env=False) as http:
        assert http.get("/healthz").status_code == 200
        console = http.get("/console")
        assert console.status_code == 200
        assert "agent-plane" in console.text and "Authority leases" in console.text
        assert http.get("/v1/audit").status_code == 401
        policies = http.get("/admin/policies", headers=admin_headers)
        assert policies.status_code == 200 and policies.json()["rules"]
        lease = {
            "id": identity, "subject": identity, "task": identity, "tenant": "smoke",
            "resources": ["staging/*"],
            "actions": ["deployment.read", "deployment.restart"],
            "protected_resources": ["staging/locked/*"],
            "require_approval": ["deployment.restart"],
            "expires_at": expiry.isoformat(), "child_authority": "none",
        }
        http.post("/v1/leases", headers=admin_headers, json=lease).raise_for_status()
        observed = []
        cases = [
            ("deployment.read", "staging/checkout", "allow", "ACTION_WITHIN_TASK_AUTHORITY"),
            ("deployment.restart", "staging/checkout", "approval_required", "ACTION_REQUIRES_APPROVAL"),
            ("deployment.read", "production/checkout", "deny", "RESOURCE_OUTSIDE_DELEGATED_SCOPE"),
            ("deployment.read", "staging/locked/checkout", "deny", "RESOURCE_PROTECTED"),
        ]
        with AgentPlane(base_url, token) as client:
            for action, resource, expected, reason in cases:
                result = client.authorize(task=identity, action=action, resource=resource)
                assert result.decision == expected, result
                assert result.reason == reason, result
                assert result.allowed is (expected == "allow")
                observed.append(result.evidence_id)
        audit = http.get("/v1/audit?limit=50", headers=admin_headers)
        audit.raise_for_status()
        events = {event["decision_id"]: event for event in audit.json()["events"]}
        assert set(observed) <= events.keys()
        assert all(events[event_id]["signature"] for event_id in observed)
        current_lease = http.get(f"/v1/leases/{identity}", headers=admin_headers)
        assert current_lease.status_code == 200 and current_lease.json()["subject"] == identity
    return {"lease_id": identity, "evidence_ids": observed}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    exercise_runtime(args.url, os.environ["ADMIN_TOKEN"], os.environ["JWT_SECRET"])
    print("PASS: console, default policies, SDK allow/approval/deny, leases, signed audit records")


if __name__ == "__main__":
    main()
