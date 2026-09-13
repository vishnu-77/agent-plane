"""Draft consequence bounds from recorded decision models, never from frequency alone."""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from agent_plane.consequence.catalog import (
    IMPACT_RANK, PERSISTENCE_RANK, REVERSIBILITY_RANK, Consequence,
)
from agent_plane.consequence.envelope import ConsequenceEnvelope

# A suggestion must not normalize production/customer/irreversible activity into
# a grant merely because it happened often. Operators can author a wider rule.
REVIEW_CEILING = ConsequenceEnvelope(
    max_impact="medium", customer_facing=False,
    max_reversibility="recoverable", max_persistence="durable",
)


def literal_resource(value: str) -> str:
    """Exact resource name represented safely in the rule's fnmatch language."""
    return value.replace("[", "[[]").replace("*", "[*]").replace("?", "[?]")


def synthesize_envelope(events: list[dict[str, Any]], *, project: str,
                        agent: str | None, actions: set[str]) -> dict[str, Any]:
    accepted: list[Consequence] = []
    evidence: list[dict[str, Any]] = []
    known: set[str] = set()
    risky: set[str] = set()
    protected: set[str] = set()
    resources: set[str] = set()
    seen: set[str] = set()
    for event in events:
        if event.get("tenant") != project or (agent and event.get("agent_id") != agent):
            continue
        trace = next((t for t in event.get("obligations_applied", [])
                      if isinstance(t, dict) and t.get("schema") == "agent-plane.trace.v1"), None)
        if not trace or not event.get("decision_id") or event["decision_id"] in seen:
            continue
        identity = trace.get("identity", {})
        action = trace.get("action", {}).get("name")
        resource = trace.get("resource", {}).get("name")
        if (identity.get("tenant") != project or (agent and identity.get("agent") != agent)
                or action not in actions or not isinstance(resource, str) or not resource):
            continue
        seen.add(event["decision_id"])
        resources.add(literal_resource(resource))
        reasons: list[str] = []
        consequence = None
        try:
            consequence = Consequence.model_validate(trace.get("consequence"))
            if consequence.action != action or consequence.resource != resource:
                raise ValueError("consequence identity mismatch")
            if not consequence.resource_profile or not consequence.action_profile:
                reasons.append("Action or resource profile was not recorded")
            if "unknown" in (consequence.environments or [consequence.environment]):
                reasons.append("Environment is unknown")
            reasons.extend(REVIEW_CEILING.violated_by(consequence))
            if consequence.environment == "production" or "production" in consequence.environments:
                reasons.append("Production access requires an operator-authored boundary")
            if consequence.protected:
                reasons.append("Protected target or downstream resource")
                protected.add(literal_resource(resource))
            if consequence.consequence_class in {"credential_access", "data_egress", "destructive_resource_change"}:
                reasons.append("Sensitive or destructive consequence requires explicit review")
            if any(not path.binding for path in consequence.paths):
                reasons.append("Reachability includes non-binding evidence")
            reason = trace.get("decision", {}).get("reason", "")
            if reason in {"CONSEQUENCE_OUTSIDE_TASK_BOUNDARY", "RESOURCE_PROTECTED", "ACTION_REFUSED_BY_RULE"}:
                reasons.append("A recorded authority boundary refused this action")
        except (ValidationError, ValueError, TypeError):
            reasons.append("Valid consequence evidence was not recorded")
        if reasons:
            risky.add(action)
        else:
            accepted.append(consequence)
            if consequence.effect in {"read", "list"} and consequence.consequence_class == "read_only":
                known.add(action)
        evidence.append({
            "decision_id": event["decision_id"], "recorded_at": event.get("created_at"),
            "task": trace.get("task", {}).get("id"), "action": action, "resource": resource,
            "included_in_envelope": not reasons, "review_reasons": reasons,
            "resource_profile": consequence.resource_profile if consequence else None,
            "action_profile": consequence.action_profile if consequence else None,
        })

    # Least upper bounds of the eligible recorded models; absent evidence gets
    # an empty environment set, which grants nothing, rather than an unbounded {}.
    envelope = ConsequenceEnvelope(
        max_impact=max((c.impact for c in accepted), key=IMPACT_RANK.__getitem__, default="none"),
        environments=sorted({e for c in accepted for e in (c.environments or [c.environment])}),
        customer_facing=False,
        max_reversibility=max((c.reversibility for c in accepted), key=REVERSIBILITY_RANK.__getitem__, default="reversible"),
        max_persistence=max((c.persistence for c in accepted), key=PERSISTENCE_RANK.__getitem__, default="transient"),
        max_blast_radius=max((c.blast_radius for c in accepted), default=0),
        max_depth=max((p.depth for c in accepted for p in c.paths), default=0),
        allowed_terminal_resources=sorted({literal_resource(p.terminal) for c in accepted for p in c.paths}),
    )
    return {
        "envelope": envelope.model_dump(exclude_none=True),
        "known_actions": known - risky, "review_actions": risky | (actions - known),
        "resources": sorted(resources), "protected_resources": sorted(protected),
        "evidence": evidence,
        "unrecorded_actions": sorted(actions - {e["action"] for e in evidence}),
        "eligible_count": len(accepted),
    }
