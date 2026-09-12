"""Connectors: how agent-plane sees a running agent.

An API key authenticates a source. It does not by itself produce telemetry,
so each integration has to say explicitly how agent-plane observes execution
and whether it can interrupt it:

    claude-code   observation full       enforcement partial   (pre-tool hook)
    codex         observation full       enforcement partial   (pre-exec hook)
    cursor        observation partial    enforcement advisory
    mcp           observation full       enforcement full      (gateway)
    sdk/custom    application-defined    enforcement advisory

``agentplane connect <target> --key ap_live_…`` installs one of these.
"""
from agent_plane.connect.credentials import Credentials, load_credentials, save_credentials

__all__ = ["Credentials", "load_credentials", "save_credentials"]
