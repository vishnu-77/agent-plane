"""The three demo scenarios. Deterministic; every step names its expected
outcome so the harness can assert the engine behaves as the story says."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEMO_TENANT = "demo"


@dataclass(frozen=True)
class Step:
    agent: str
    action: str
    resource: str
    expected: str                      # allow | deny | approval_required
    note: str = ""
    impact: str = "reversible"
    spawn: dict[str, Any] | None = None   # delegate a child lease before acting


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    prompt: str
    created_by: str
    task: str
    agent: str
    framework: str
    lease: dict[str, Any]
    steps: list[Step] = field(default_factory=list)
    summary: str = ""


SCENARIOS: dict[str, Scenario] = {
    "staging-incident": Scenario(
        name="staging-incident",
        title="Staging incident",
        prompt="Investigate why checkout is failing in staging. Restart checkout if required. Do not touch production.",
        created_by="human:oncall-01",
        task="incident-218",
        agent="incident-agent",
        framework="langgraph",
        lease={
            "id": "demo-lease-incident-218", "task": "incident-218", "subject": "incident-agent",
            "resources": ["staging/checkout", "staging/checkout/*"],
            "actions": ["logs.read", "metrics.read", "deployment.read", "deployment.restart"],
            "protected_resources": ["production/*"],
            "permitted_consequence": {"environments": ["staging"], "customer_facing": False,
                                      "max_impact": "medium"},
            "maximum_impact": "reversible", "child_authority": "subset_only",
        },
        steps=[
            Step("incident-agent", "logs.read", "staging/checkout", "allow", "read the failing service's logs"),
            Step("incident-agent", "metrics.read", "staging/checkout", "allow", "confirm the error rate"),
            Step("incident-agent", "deployment.restart", "staging/checkout", "allow", "restart the staging workload"),
            Step("incident-agent", "deployment.restart", "production/checkout", "deny",
                 "the model proposes restarting production too"),
        ],
        summary="Same credential, same verb. Staging restart is inside the task; production restart is outside it and would interrupt a customer-facing service.",
    ),
    "github-maintenance": Scenario(
        name="github-maintenance",
        title="GitHub maintenance",
        prompt="Remove stale branches from this repository. Never modify main.",
        created_by="human:maintainer-02",
        task="repo-maintenance-77",
        agent="repo-agent",
        framework="claude-code",
        lease={
            "id": "demo-lease-repo-maintenance-77", "task": "repo-maintenance-77", "subject": "repo-agent",
            "resources": ["github://demo/agent-plane", "github://demo/agent-plane/*"],
            "actions": ["repository.read", "branch.list", "branch.delete"],
            "protected_resources": ["github://demo/agent-plane/branches/main"],
            "max_uses": {"branch.delete": 5},
            "permitted_consequence": {"max_impact": "medium", "max_blast_radius": 1},
            "maximum_impact": "reversible", "child_authority": "none",
        },
        steps=[
            Step("repo-agent", "branch.list", "github://demo/agent-plane", "allow", "enumerate branches"),
            Step("repo-agent", "branch.delete", "github://demo/agent-plane/branches/stale-feature", "allow",
                 "delete a stale branch: low downstream consequence"),
            Step("repo-agent", "branch.delete", "github://demo/agent-plane/branches/main", "deny",
                 "main is protected; deleting it removes the default branch and breaks CI/CD and deployment"),
        ],
        summary="Stale-branch deletion and main-branch deletion are the same verb with different consequences: one is a housekeeping change, the other removes the source of every deployment.",
    ),
    "delegation": Scenario(
        name="delegation",
        title="Multi-agent delegation",
        prompt="Investigate the checkout latency regression. Report findings; do not change anything.",
        created_by="human:sre-lead",
        task="latency-review-9",
        agent="incident-agent",
        framework="langgraph",
        lease={
            "id": "demo-lease-latency-review-9", "task": "latency-review-9", "subject": "incident-agent",
            "resources": ["staging/*", "production/*"],
            "actions": ["logs.read", "metrics.read", "deployment.read"],
            "permitted_consequence": {"max_impact": "none"},
            "maximum_impact": "reversible", "child_authority": "subset_only",
        },
        steps=[
            Step("incident-agent", "metrics.read", "production/checkout", "allow", "read production latency"),
            Step("metrics-agent", "metrics.read", "production/checkout", "allow",
                 "the child reads metrics under its attenuated lease",
                 spawn={"agent": "metrics-agent", "actions": ["metrics.read"], "id": "demo-lease-metrics-agent-9"}),
            Step("metrics-agent", "deployment.restart", "production/checkout", "deny",
                 "the child proposes a restart; no ancestor authority includes deployment.restart"),
        ],
        summary="Delegation only attenuates. The child never held deployment.restart, and neither did its parent, so the lineage explains the denial end to end.",
    ),
}
