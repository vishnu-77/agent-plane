"""Software engineering domain pack (Phase 27). Matches the action
namespaces already used throughout this repo's own config/tools.yaml and
demo/benchmark scenarios - this pack names the convention, it doesn't
invent a new one.
"""
from __future__ import annotations

from agent_plane.authority.contract import AuthorityContract

ACTIONS = [
    "filesystem.read", "filesystem.write", "repository.read", "repository.write",
    "repository.delete", "git.commit", "git.push", "tests.execute", "ci.trigger", "deployment.restart",
]


class SoftwareEngineeringAdapter:
    domain = "software"

    def actions(self) -> list[str]:
        return list(ACTIONS)

    def parse_resource(self, raw: str) -> str:
        """workspace-relative paths, matching the convention
        agent_plane.events.normalize already uses for filesystem/repository
        resources."""
        raw = raw.strip().strip("/")
        return raw if "/" in raw else f"workspace/{raw}"

    def suggested_contract(self, agent: str, *, tenant: str = "default") -> AuthorityContract:
        """The spec/authority-contract.md example, as a callable default -
        development-only, no secrets, ask before pushing."""
        return AuthorityContract(
            contract_id=f"{agent}-software-starter", agent=agent, tenant=tenant,
            allow=["filesystem.read", "filesystem.write", "tests.execute"],
            ask_first=["git.push"],
            never=["repository.delete", "secrets.read"],
            resources_allow=["workspace/**"],
            resources_protected=["workspace/.env*", "production/**"],
            consequence_environments=["development"], max_reversibility="reversible",
        )
