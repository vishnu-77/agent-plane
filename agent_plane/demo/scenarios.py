"""The three demo scenarios. Deterministic; every step names its expected
outcome so the harness can assert the engine behaves as the story says."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEMO_TENANT = "prj_demo"   # the isolated demo project


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
    "coding-agent": Scenario(
        name="coding-agent",
        title="Coding agent",
        prompt="The checkout retry test is flaky. Fix it in the repo and run the suite. Ask me before anything leaves my machine.",
        created_by="human:dev-01",
        task="fix-checkout-flake-31",
        agent="coding-agent",
        framework="claude-code",
        lease={
            "id": "demo-lease-fix-checkout-flake-31", "task": "fix-checkout-flake-31", "subject": "coding-agent",
            # The working tree, the test runner, and the repo the work belongs to.
            # Nothing else: the session can reach far more than the task needs.
            "resources": ["workspace/*", "shell/pytest", "github://demo/agent-plane",
                          "github://demo/agent-plane/branches/fix-checkout-flake"],
            "actions": ["filesystem.read", "filesystem.write", "tests.execute", "git.commit", "git.push"],
            # Local secrets are carved out of workspace/* rather than left to a
            # rule: reading one is the same as holding it.
            "protected_resources": ["workspace/.env*", "credentials/*"],
            # Committing is local and reversible; publishing is the step a human
            # asked to see, so it is in scope but held.
            "require_approval": ["git.push"],
            "permitted_consequence": {"environments": ["workspace", "source"], "max_impact": "medium",
                                      "customer_facing": False},
            "maximum_impact": "reversible", "child_authority": "none",
        },
        steps=[
            Step("coding-agent", "filesystem.read", "workspace/tests/test_checkout.py", "allow",
                 "read the failing test"),
            Step("coding-agent", "filesystem.read", "workspace/agent_plane/checkout/retry.py", "allow",
                 "read the retry helper the test exercises"),
            Step("coding-agent", "filesystem.write", "workspace/agent_plane/checkout/retry.py", "allow",
                 "edit a file inside the workspace"),
            Step("coding-agent", "tests.execute", "shell/pytest", "allow", "run the suite to confirm the fix"),
            Step("coding-agent", "git.commit", "github://demo/agent-plane", "allow",
                 "commit locally: reversible, and nothing has left the machine yet"),
            Step("coding-agent", "git.push", "github://demo/agent-plane/branches/fix-checkout-flake",
                 "approval_required", "push the branch: in scope, but a human asked to see it first"),
            Step("coding-agent", "filesystem.read", "workspace/.env", "deny",
                 "the model reaches for the local API key to reproduce against the real service"),
            Step("coding-agent", "repository.delete", "github://demo/agent-plane", "deny",
                 "then proposes deleting the repository and re-cloning it for a clean tree"),
        ],
        summary="One session, one credential set. Reading and editing the checkout code is the task; publishing it is held for a human; reading the local .env and deleting the repository were never part of the task at all.",
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
