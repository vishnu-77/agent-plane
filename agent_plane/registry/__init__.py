"""Agent Registry: who exists, why it is running, what it may do, what it did.

Agents are *discovered*, not pre-registered. Every governed call (authorize,
MCP admission, broker, model proxy) upserts the acting agent, its session,
and its task; leases and delegations attach granted and delegated authority;
decisions attach exercised authority, resources touched, and drift between
declared, granted, and observed capability.

Hierarchy:

    Tenant
    └── Application
        └── Agent
            └── Session
                └── Task
                    └── AuthorityLease
"""
from agent_plane.registry.store import (
    AgentRecord,
    MemoryRegistry,
    SessionRecord,
    SqlRegistry,
    TaskRecord,
    build_registry,
)

__all__ = [
    "AgentRecord",
    "MemoryRegistry",
    "SessionRecord",
    "SqlRegistry",
    "TaskRecord",
    "build_registry",
]
