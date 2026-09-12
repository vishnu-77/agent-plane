"""Action events: how agent-plane sees what an agent actually did.

An API key authenticates a source; it does not by itself produce telemetry.
Every integration reports through one primitive, ``POST /v1/events/action``,
which normalizes a raw tool invocation into a canonical action and resource,
runs the decision pipeline, and records the evidence.
"""
from agent_plane.events.normalize import normalize_action

__all__ = ["normalize_action"]
