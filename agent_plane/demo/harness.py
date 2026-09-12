"""Deterministic agent harness for the hosted demo.

Runs a scenario's steps through the real :class:`AuthorityService` in the
isolated demo tenant. ALLOW outcomes "execute" against simulated targets
and record an execution receipt on the audit chain; every other outcome
records nothing but the decision, exactly like a real executor must.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_plane.authority.lease import AuthorityLease, parse_lease
from agent_plane.demo.scenarios import DEMO_TENANT, SCENARIOS, Scenario
from agent_plane.registry.store import Origin
from agent_plane.schemas.canonical import Actor


class SimulatedTargets:
    """In-memory stand-ins for a working tree, staging, production, and GitHub.

    Nothing here touches the disk, the network, or a real repository: an ALLOW
    "executes" by moving a number in this object, so a hosted demo can run the
    real engine without any external side effect.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.deployments: dict[str, dict[str, Any]] = {
            "staging/checkout": {"status": "degraded", "restarts": 0},
            "production/checkout": {"status": "healthy", "restarts": 0},
        }
        self.branches: list[str] = ["main", "stale-feature", "feature/latency"]
        # The coding agent's working tree: file paths and revision counters, no
        # contents. A write moves a revision; it never reaches a real file.
        self.files: dict[str, dict[str, Any]] = {
            "tests/test_checkout.py": {"revision": 1},
            "agent_plane/checkout/retry.py": {"revision": 3},
        }
        self.commits: list[dict[str, Any]] = []
        self.edits = 0
        self.log: list[dict[str, Any]] = []

    def execute(self, action: str, resource: str) -> dict[str, Any]:
        if action == "deployment.restart":
            dep = self.deployments.setdefault(resource, {"status": "healthy", "restarts": 0})
            dep["restarts"] += 1
            dep["status"] = "healthy"
            result = {"restarted": resource, "status": dep["status"]}
        elif action == "branch.delete":
            branch = resource.rsplit("/", 1)[-1]
            if branch in self.branches:
                self.branches.remove(branch)
            result = {"deleted": branch, "remaining": list(self.branches)}
        elif action == "branch.list":
            result = {"branches": list(self.branches)}
        elif action == "filesystem.read":
            path = resource.removeprefix("workspace/")
            result = {"read": path, "revision": self.files.get(path, {}).get("revision", 0)}
        elif action == "filesystem.write":
            path = resource.removeprefix("workspace/")
            entry = self.files.setdefault(path, {"revision": 0})
            entry["revision"] += 1
            self.edits += 1
            result = {"edited": path, "revision": entry["revision"]}
        elif action == "tests.execute":
            # Deterministic and it follows the story: the flaky test fails until
            # the edit lands.
            failed = 0 if self.edits else 1
            result = {"suite": resource, "passed": 128 - failed, "failed": failed}
        elif action == "git.commit":
            self.commits.append({"message": "fix flaky checkout retry", "files": self.edits})
            result = {"committed": len(self.commits), "files": self.edits}
        elif action in ("logs.read", "metrics.read", "deployment.read", "repository.read"):
            result = {"read": resource, "sample": {"error_rate": 0.42, "p95_ms": 1830}
                      if resource.startswith("staging") else {"error_rate": 0.01, "p95_ms": 210}}
        else:
            result = {"executed": action, "on": resource}
        self.log.append({"action": action, "resource": resource, "result": result,
                         "at": datetime.now(UTC).isoformat()})
        return result


class DemoHarness:
    def __init__(self, state: Any):
        self.state = state
        self.targets = SimulatedTargets()

    # -- lifecycle -------------------------------------------------------------- #
    def reset(self) -> dict[str, Any]:
        leases = self.state.leases
        removed = 0
        for lease in leases.list():
            if lease.tenant == DEMO_TENANT:
                leases.revoke(lease.id)
                removed += 1
        self.state.agent_registry.reset_tenant(DEMO_TENANT)
        self.targets.reset()
        return {"reset": True, "tenant": DEMO_TENANT, "leases_revoked": removed}

    # -- helpers -------------------------------------------------------------------- #
    def _actor(self, scenario: Scenario, agent: str, declared: list[str]) -> Actor:
        return Actor(user_id=scenario.created_by, tenant=DEMO_TENANT, app_id="demo-harness",
                     agent_id=agent, allowed_tools=declared)

    def _fresh_lease(self, scenario: Scenario, run_id: str) -> AuthorityLease:
        doc = dict(scenario.lease)
        doc["id"] = f"{doc['id']}-{run_id}"
        doc["tenant"] = DEMO_TENANT
        doc["expires_at"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        doc["origin"] = {"kind": "human", "ref": f"prompt_{run_id}", "created_by": scenario.created_by,
                         "text": scenario.prompt}
        return parse_lease(doc)

    def _spawn(self, parent: AuthorityLease, spec: dict[str, Any], run_id: str) -> AuthorityLease:
        child = AuthorityLease(
            id=f"{spec.get('id', 'demo-lease-child')}-{run_id}", task=parent.task, subject=spec["agent"],
            tenant=parent.tenant, resources=list(spec.get("resources") or parent.resources),
            actions=list(spec.get("actions") or parent.actions),
            protected_resources=list(parent.protected_resources), max_uses=dict(parent.max_uses),
            require_approval=list(parent.require_approval), expires_at=parent.expires_at,
            maximum_impact=parent.maximum_impact, child_authority="none", parent_lease=parent.id,
            origin={"kind": "parent", "ref": parent.id, "created_by": parent.subject},
            permitted_consequence=dict(parent.permitted_consequence),
        )
        self.state.leases.add(child)
        self.state.agent_registry.attach_lease(tenant=DEMO_TENANT, task=parent.task, agent=child.subject,
                                               lease_id=child.id, parent_agent=parent.subject)
        return child

    def _receipt(self, actor: Actor, decision_id: str, action: str, resource: str, result: dict[str, Any]) -> None:
        self.state.audit.record({
            "decision_id": f"gx_{uuid.uuid4().hex[:16]}", "user_id": actor.user_id, "tenant": actor.tenant,
            "agent_id": actor.agent_id, "app_id": actor.app_id,
            "model_requested": f"gateway-receipt:{action}", "model_used": resource, "data_classification": "",
            "decision": "allow", "reason": "UPSTREAM_RESULT_RECEIVED",
            "obligations_applied": [{"schema": "agent-plane.gateway.v1", "phase": "execution",
                                     "execution_status": "completed", "admission_id": decision_id,
                                     "action": action, "resource": resource, "tool": "simulated",
                                     "protocol_surface": "demo", "mode": "enforce", "task": "",
                                     "result": result, "recorded_at": datetime.now(UTC).isoformat()}],
            "rules_matched": [],
        })

    # -- running ---------------------------------------------------------------------- #
    def run(self, name: str, *, steps: list[int] | None = None, run_id: str | None = None) -> dict[str, Any]:
        scenario = SCENARIOS.get(name)
        if scenario is None:
            raise KeyError(name)
        run_id = run_id or uuid.uuid4().hex[:6]
        service = self.state.authority
        registry = self.state.agent_registry

        # Intent -> task -> lease, recorded with provenance.
        registry.register_task(
            tenant=DEMO_TENANT, task=scenario.task, agent=scenario.agent,
            origin=Origin(kind="human", ref=f"prompt_{run_id}", text=scenario.prompt,
                          created_by=scenario.created_by))
        # Steps may arrive one call at a time (the console animates them), so the
        # root and child leases of a run are looked up by run id, not kept in memory.
        root = self._fresh_lease(scenario, run_id)
        existing_root = self.state.leases.get(root.id)
        if existing_root is None or existing_root.revoked:
            self.state.leases.add(root)
        else:
            root = existing_root
        registry.attach_lease(tenant=DEMO_TENANT, task=scenario.task, agent=scenario.agent, lease_id=root.id)
        # The identity layer's static grant: a coding agent's session declares its
        # whole toolbelt, and the lease is what narrows it for this task. Deriving
        # the manifest from the run keeps the demo on the interesting gate - an
        # action the agent can technically perform is still outside task authority.
        declared = sorted({a.split(".", 1)[0] for a in root.actions}
                          | {st.action.split(".", 1)[0] for st in scenario.steps})

        results: list[dict[str, Any]] = []
        children: dict[str, AuthorityLease] = {
            ls.subject: ls for ls in self.state.leases.list()
            if ls.tenant == DEMO_TENANT and ls.parent_lease == root.id and not ls.revoked
        }
        for index, step in enumerate(scenario.steps):
            if steps is not None and index not in steps:
                continue
            if step.spawn and step.spawn["agent"] not in children:
                children[step.spawn["agent"]] = self._spawn(root, step.spawn, run_id)
            actor = self._actor(scenario, step.agent, declared)
            lease_ids = frozenset([children[step.agent].id]) if step.agent in children else frozenset([root.id])
            outcome = service.decide(
                actor, task=scenario.task, action=step.action, resource=step.resource, impact=step.impact,
                context={"framework": scenario.framework, "origin": scenario.created_by,
                         "prompt_hash": f"prompt_{run_id}", "run_id": run_id,
                         **({"parent_agent": scenario.agent} if step.agent in children else {})},
                edge="demo", lease_ids=lease_ids,
            )
            executed = None
            if outcome.outcome.value == "allow":
                executed = self.targets.execute(step.action, step.resource)
                self._receipt(actor, outcome.decision_id, step.action, step.resource, executed)
            results.append({
                "index": index, "agent": step.agent, "action": step.action, "resource": step.resource,
                "note": step.note, "expected": step.expected, "outcome": outcome.outcome.value,
                "reason": outcome.reason, "decision_id": outcome.decision_id, "lease": outcome.lease_id,
                "approval_id": outcome.approval_id,
                "matches_expected": outcome.outcome.value == step.expected or
                (outcome.outcome.value == "simulate" and outcome.would_be == step.expected),
                "explanation": outcome.trace["explanation"], "consequence": outcome.payload.get("consequence"),
                "executed": executed, "lineage": outcome.trace["authority"]["lineage"],
            })
        return {"scenario": scenario.name, "title": scenario.title, "prompt": scenario.prompt,
                "task": scenario.task, "agent": scenario.agent, "run_id": run_id, "lease": root.id,
                "children": {k: v.id for k, v in children.items()}, "steps": results,
                "summary": scenario.summary,
                "targets": {"deployments": self.targets.deployments, "branches": self.targets.branches,
                            "workspace": self.targets.files, "commits": self.targets.commits}}

    @staticmethod
    def describe() -> list[dict[str, Any]]:
        return [{
            "name": s.name, "title": s.title, "prompt": s.prompt, "task": s.task, "agent": s.agent,
            "framework": s.framework, "created_by": s.created_by, "summary": s.summary,
            "authority": {"actions": s.lease["actions"], "resources": s.lease["resources"],
                          "protected_resources": s.lease.get("protected_resources", []),
                          # Actions that are granted but held for a human, so the
                          # description of the authority matches what the run does.
                          "require_approval": s.lease.get("require_approval", []),
                          "permitted_consequence": s.lease.get("permitted_consequence", {})},
            "steps": [{"index": i, "agent": st.agent, "action": st.action, "resource": st.resource,
                       "expected": st.expected, "note": st.note, "spawns": st.spawn["agent"] if st.spawn else None}
                      for i, st in enumerate(s.steps)],
        } for s in SCENARIOS.values()]
