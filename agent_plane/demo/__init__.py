"""Hosted DEMO mode: deterministic scenarios through the real authority engine.

Nothing here is browser-only data. Each scenario registers an intent,
issues real leases in the isolated ``demo`` tenant, mints a real agent
identity, and pushes real actions through :class:`AuthorityService`. Only
the *targets* are simulated: an ALLOW "executes" against an in-memory model
of a working tree, GitHub, staging, and production, and records an execution
receipt on the audit chain like the MCP gateway does.
"""
from agent_plane.demo.scenarios import SCENARIOS, Scenario, Step

__all__ = ["SCENARIOS", "Scenario", "Step"]
