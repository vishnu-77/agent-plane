"""DomainAdapter interface. See agent_plane/domains/__init__.py."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_plane.authority.contract import AuthorityContract


@runtime_checkable
class DomainAdapter(Protocol):
    domain: str

    def actions(self) -> list[str]:
        """The action namespace this domain pack claims (e.g.
        ["filesystem.*", "repository.*"]) - informational/discovery, not
        enforced here; the evaluator only ever consults a compiled lease."""
        ...

    def parse_resource(self, raw: str) -> str:
        """This domain's convention for turning a raw identifier (a file
        path, a cloud ARN, an account number) into the canonical resource
        string ConsequenceCatalog/AuthorityLease glob-match against."""
        ...

    def suggested_contract(self, agent: str, *, tenant: str = "default") -> AuthorityContract:
        """A starter AuthorityContract for this domain - Observe -> Suggest
        (Phase 18), not something enforced until a human reviews and
        compiles it via agent_plane.authority.contract.compile_contract."""
        ...
