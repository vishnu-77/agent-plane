"""Accounts: the product's tenancy and credential model.

    User
      └── Workspace (via Membership)
            └── Project          ← the security and data boundary
                  ├── ApiKey     ap_live_ / ap_test_ / ap_mgmt_
                  └── Integration

A **Project** is the tenant: every agent, task, lease, decision, and audit
record is scoped to one project id. Humans use the console through an ordinary
signed session; agents and connectors use a Project API Key. Nobody pastes an
admin token to use the product.
"""
from agent_plane.accounts.models import ApiKey, Integration, Membership, Project, User, Workspace
from agent_plane.accounts.store import AccountStore, build_account_store

__all__ = [
    "AccountStore",
    "ApiKey",
    "Integration",
    "Membership",
    "Project",
    "User",
    "Workspace",
    "build_account_store",
]
