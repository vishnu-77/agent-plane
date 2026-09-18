"""Principal identity: who/what is acting, and how strongly we know it.

See spec/principals.md and spec/identity-assurance.md.

No eager re-exports here (deliberately - agent_plane.schemas.canonical
imports agent_plane.identity.assurance for Actor.assurance, and
agent_plane.identity.resolver imports Actor back; re-exporting resolver's
names at package-init time would make importing the leaf assurance module
cascade into a circular import). Import submodules directly:
``from agent_plane.identity.assurance import IdentityAssurance``,
``from agent_plane.identity.models import PrincipalIdentity``, etc.
"""
from __future__ import annotations
