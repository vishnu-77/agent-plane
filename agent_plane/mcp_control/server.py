"""Agent Plane Control MCP (Phase 19): an agent introspecting *itself*
against the platform's own decisions/authority - "why couldn't I push?" ->
"the active contract permits git.commit but requires approval for
git.push" - not enforcement (that's agent_plane/gateway/mcp.py's job,
unrelated to this module and currently broken against the installed SDK -
see that file's own note).

Built on mcp.server.fastmcp.FastMCP, the SDK's high-level ergonomic API,
confirmed to match the installed mcp package (v1.26.0) by direct
introspection - unlike gateway/mcp.py's `from mcp import Client, types`
and `Server(..., on_list_tools=..., on_call_tool=...)`, neither of which
exist in this SDK version. See tests/test_mcp_control.py, which exercises
every tool through FastMCP's real call_tool() dispatch, not by calling the
Python functions directly, so the SDK wiring itself is verified.

Deliberately not mounted into the main ASGI app by default (build_control_
server() returns the FastMCP instance; call .streamable_http_app() to get
a mountable app). Mounting a second ASGI app with its own session-manager
lifespan into the existing, heavily-tested FastAPI app is a distinct,
riskier step that deserves its own dedicated verification - not something
to fold into the already-large set of changes in this pass. Every tool
takes its subject explicitly (agent_id/decision_id) rather than binding to
a per-session caller identity, for the same reason: real per-caller
identity binding over an MCP session is follow-up work, not this pass's.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from agent_plane.authority.service import TRACE_SCHEMA
from agent_plane.registry.store import agent_lifecycle_state


def build_control_server(app_state: Any) -> FastMCP:
    mcp = FastMCP(
        "agent-plane-control",
        instructions="Introspect Agent Plane's own decisions and authority state for one agent.",
    )

    @mcp.tool()
    def whoami(agent_id: str, tenant: str = "default") -> dict[str, Any]:
        """Who is this agent, per the registry: definition, lifecycle
        state, identity assurance, trust domain."""
        registry = app_state.agent_registry
        rec = registry.agent(tenant, agent_id)
        if rec is None:
            return {"found": False}
        definition = registry.definition(tenant, rec.definition_id) if rec.definition_id else None
        principal = registry.principal(tenant, agent_id)
        return {
            "found": True,
            "agent_id": rec.id,
            "definition": definition.model_dump(mode="json") if definition else None,
            "lifecycle_state": agent_lifecycle_state(rec, definition, []),
            "assurance": principal.assurance if principal else None,
            "trust_domain": principal.trust_domain if principal else None,
            "status": rec.status,
        }

    @mcp.tool()
    def authority(agent_id: str, tenant: str = "default") -> dict[str, Any]:
        """What this agent is currently authorised to do - the same
        declared/granted/observed/undeclared/ungranted/capability_outside_
        authority/unused_authority drift view GET /v1/agents/{id}/drift
        returns."""
        registry = app_state.agent_registry
        leases = app_state.leases
        granted = sorted({a for ls in leases.list()
                          if ls.subject == agent_id and ls.tenant == tenant and not ls.revoked
                          for a in ls.actions})
        return registry.drift(tenant, agent_id, granted)

    @mcp.tool()
    def explain_decision(decision_id: str) -> dict[str, Any]:
        """Why one specific decision was allowed, denied, or required
        approval - the full trace GET /v1/decisions/{id} returns."""
        event = app_state.audit.get(decision_id)
        if event is None:
            return {"found": False}
        trace = next((item for item in event.get("obligations_applied") or []
                     if isinstance(item, dict) and item.get("schema") == TRACE_SCHEMA), None)
        return {"found": True, "decision": event.get("decision"), "reason": event.get("reason"), "trace": trace}

    @mcp.tool()
    def current_contract(agent_id: str, tenant: str = "default") -> dict[str, Any]:
        """The latest Authority Contract governing this agent, if any was
        issued with contract_id "<agent_id>-<domain>-starter" (the
        convention agent_plane.domains.{software,cloud}'s
        suggested_contract() uses) or explicitly passed via contract_id."""
        registry = getattr(app_state, "contracts", None)
        if registry is None:
            return {"found": False}
        for suffix in ("-software-starter", "-cloud-starter"):
            contract = registry.latest(tenant, f"{agent_id}{suffix}")
            if contract is not None:
                return {"found": True, "contract": contract.model_dump(mode="json"),
                        "fingerprint": contract.fingerprint}
        return {"found": False}

    return mcp
