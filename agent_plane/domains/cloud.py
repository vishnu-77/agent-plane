"""Cloud operations domain pack (Phase 27)."""
from __future__ import annotations

from agent_plane.authority.contract import AuthorityContract

ACTIONS = [
    "deployment.restart", "deployment.deploy", "infrastructure.provision", "infrastructure.destroy",
    "secrets.read", "secrets.write", "identity.grant", "identity.revoke", "database.read", "database.write",
]


class CloudOperationsAdapter:
    domain = "cloud"

    def actions(self) -> list[str]:
        return list(ACTIONS)

    def parse_resource(self, raw: str) -> str:
        """environment/service, matching the "staging/*", "production/*"
        convention config/leases.yaml's shipped demo data already uses."""
        raw = raw.strip().strip("/")
        return raw if "/" in raw else f"staging/{raw}"

    def suggested_contract(self, agent: str, *, tenant: str = "default") -> AuthorityContract:
        """Cautious by default: read/restart in staging, everything
        production-facing or secret-touching needs a human, deletion is
        never automatic."""
        return AuthorityContract(
            contract_id=f"{agent}-cloud-starter", agent=agent, tenant=tenant,
            allow=["deployment.restart", "database.read"],
            ask_first=["deployment.deploy", "secrets.read", "infrastructure.provision"],
            never=["infrastructure.destroy", "identity.grant", "secrets.write"],
            resources_allow=["staging/*"],
            resources_protected=["production/*"],
            consequence_environments=["staging"], max_reversibility="reversible",
        )
