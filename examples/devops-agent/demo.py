"""The killer demo: capability != authority.

`devops-agent` holds real `deployment.*` capability (its credential can
legitimately restart or delete deployments), but the lease issued for THIS
task (`fix-staging-checkout`, see config/leases.yaml) only authorises
`staging/*` - `production/*` is explicitly protected.

Run:
    agentplane serve --reload          # shell 1, from the repo root
    python examples/devops-agent/demo.py   # shell 2
"""
from __future__ import annotations

import sys
from pathlib import Path

import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))
from agentplane import AgentPlane  # noqa: E402

JWT_SECRET = "dev-secret-change-me"  # must match the server's JWT_SECRET


def _token(agent_id: str) -> str:
    return jwt.encode({"sub": "demo-user", "agent_id": agent_id}, JWT_SECRET, algorithm="HS256")


def main() -> None:
    plane = AgentPlane("http://localhost:8000", _token("devops-agent"))

    print("Task: fix-staging-checkout\n")

    print("1) restart staging/checkout")
    d = plane.authorize(task="fix-staging-checkout", action="deployment.restart",
                         resource="staging/checkout")
    print(f"   -> {d.decision.upper()} ({d.reason})\n")
    assert d.allowed

    print("2) DELETE production/checkout  <- the LLM's actual proposal")
    d = plane.authorize(task="fix-staging-checkout", action="deployment.delete",
                         resource="production/checkout")
    print(f"   -> {d.decision.upper()} ({d.reason})")
    print("   evidence:", d.evidence_id)
    assert not d.allowed

    print("\nSame agent, same credential, same tool. Different resource, different task")
    print("authority. That's the enforcement primitive.")


if __name__ == "__main__":
    main()
