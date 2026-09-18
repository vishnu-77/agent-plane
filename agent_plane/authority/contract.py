"""Authority Contract (0.8.x, see spec/authority-contract.md): the
human-readable surface developers write, which *compiles* into the existing
AuthorityLease - no second authorization engine. The evaluator
(agent_plane.authority.evaluator) only ever sees compiled leases.

    Authority Contract  --compile-->  AuthorityLease  -->  Evaluator

Versioned like infrastructure config (Phase 14): every contract carries
contract_id/version/created_by/approved_by/created_at/effective_at/
supersedes/fingerprint, so "which policy allowed this action on <date>" has
an exact, reproducible answer - the fingerprint is a deterministic hash of
the compiled content, not of the YAML source, so two contracts that compile
to the same lease shape are recognizably identical even if formatted
differently.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from agent_plane.authority.lease import AuthorityLease


class AuthorityContract(BaseModel):
    contract_id: str
    version: int = 1
    agent: str                       # subject this contract governs
    tenant: str = "default"

    allow: list[str] = Field(default_factory=list)          # -> lease.actions
    ask_first: list[str] = Field(default_factory=list)       # -> lease.require_approval
    never: list[str] = Field(default_factory=list)           # -> lease.denied_actions

    resources_allow: list[str] = Field(default_factory=list)      # -> lease.resources
    resources_protected: list[str] = Field(default_factory=list)  # -> lease.protected_resources

    consequence_environments: list[str] | None = None        # -> permitted_consequence.environments
    max_reversibility: str | None = None                     # -> permitted_consequence.max_reversibility
    maximum_impact: str = "reversible"                        # -> lease.maximum_impact

    delegation_children: str = "subset_only"                  # -> lease.child_authority

    # Identity prerequisite passthrough (Phase 4) - a contract can require
    # it the same way a hand-built lease can.
    require_established_identity: bool = False
    min_assurance: str | None = None
    allowed_trust_domains: list[str] = Field(default_factory=list)

    created_by: str | None = None
    approved_by: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    effective_at: datetime | None = None
    supersedes: str | None = None

    @property
    def fingerprint(self) -> str:
        """Deterministic hash of the compiled-content-relevant fields - not
        of contract_id/version/approval metadata, so two contracts with
        identical governance content but different bookkeeping still share
        a fingerprint, and GRC can answer "was this exact policy content
        used before" independent of versioning."""
        canonical = {
            "agent": self.agent, "tenant": self.tenant,
            "allow": sorted(self.allow), "ask_first": sorted(self.ask_first), "never": sorted(self.never),
            "resources_allow": sorted(self.resources_allow),
            "resources_protected": sorted(self.resources_protected),
            "consequence_environments": sorted(self.consequence_environments or []),
            "max_reversibility": self.max_reversibility, "maximum_impact": self.maximum_impact,
            "delegation_children": self.delegation_children,
            "require_established_identity": self.require_established_identity,
            "min_assurance": self.min_assurance,
            "allowed_trust_domains": sorted(self.allowed_trust_domains),
        }
        blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def compile_contract(contract: AuthorityContract, *, lease_id: str, task: str) -> AuthorityLease:
    """Compile a contract into a concrete, task-bound AuthorityLease. A
    contract describes policy for a class of tasks; a lease is bound to one
    concrete task id, supplied here at compile time."""
    permitted_consequence: dict[str, Any] = {}
    if contract.consequence_environments:
        permitted_consequence["environments"] = contract.consequence_environments
    if contract.max_reversibility:
        permitted_consequence["max_reversibility"] = contract.max_reversibility
    return AuthorityLease(
        id=lease_id, task=task, subject=contract.agent, tenant=contract.tenant,
        resources=list(contract.resources_allow), actions=list(contract.allow),
        protected_resources=list(contract.resources_protected),
        denied_actions=list(contract.never),
        require_approval=list(contract.ask_first),
        maximum_impact=contract.maximum_impact,
        child_authority=contract.delegation_children,
        permitted_consequence=permitted_consequence,
        require_established_identity=contract.require_established_identity,
        min_assurance=contract.min_assurance,
        allowed_trust_domains=list(contract.allowed_trust_domains),
        origin={"kind": "authority_contract", "ref": contract.contract_id,
                "created_by": contract.created_by, "text": f"v{contract.version} ({contract.fingerprint})"},
    )


def parse_contract(doc: dict[str, Any]) -> AuthorityContract:
    """Parse the human-readable YAML/JSON shape from spec/authority-contract.md
    (agent/task/allow/ask_first/never/resources/consequence/delegation) into
    an AuthorityContract. Raises ValueError on a missing required field."""
    agent = doc.get("agent")
    contract_id = doc.get("contract_id") or doc.get("id")
    if not agent or not contract_id:
        raise ValueError("a contract requires 'contract_id' and 'agent'")
    resources = doc.get("resources") or {}
    consequence = doc.get("consequence") or {}
    delegation = doc.get("delegation") or {}
    identity = doc.get("identity") or {}
    max_reversibility = consequence.get("max_reversibility")
    if isinstance(max_reversibility, list):  # spec's example shows a one-item list
        max_reversibility = max_reversibility[0] if max_reversibility else None
    return AuthorityContract(
        contract_id=contract_id,
        version=int(doc.get("version") or 1),
        agent=agent,
        tenant=doc.get("tenant") or "default",
        allow=list(doc.get("allow") or []),
        ask_first=list(doc.get("ask_first") or []),
        never=list(doc.get("never") or []),
        resources_allow=list(resources.get("allow") or []),
        resources_protected=list(resources.get("protected") or []),
        consequence_environments=list(consequence["environments"]) if consequence.get("environments") else None,
        max_reversibility=max_reversibility,
        maximum_impact=doc.get("maximum_impact") or "reversible",
        delegation_children=delegation.get("children") or "subset_only",
        require_established_identity=bool(identity.get("require_established_identity") or False),
        min_assurance=identity.get("min_assurance"),
        allowed_trust_domains=list(identity.get("allowed_trust_domains") or []),
        created_by=doc.get("created_by"),
        approved_by=doc.get("approved_by"),
        effective_at=doc.get("effective_at"),
        supersedes=doc.get("supersedes"),
    )


class ContractRegistry:
    """In-memory contract store, keyed by (tenant, contract_id) -> latest
    version, plus every version for history/diff.

    ponytail: in-memory only, mirrors TrustDomainRegistry's precedent - add
    a ContractRow (mirrors AgentDefinitionRow in registry/store.py) when an
    operator needs contracts to survive a restart, not before.
    """

    def __init__(self) -> None:
        self._versions: dict[tuple[str, str], list[AuthorityContract]] = {}

    def upsert(self, contract: AuthorityContract) -> AuthorityContract:
        key = (contract.tenant, contract.contract_id)
        self._versions.setdefault(key, []).append(contract)
        return contract

    def latest(self, tenant: str, contract_id: str) -> AuthorityContract | None:
        versions = self._versions.get((tenant, contract_id))
        return max(versions, key=lambda c: c.version) if versions else None

    def history(self, tenant: str, contract_id: str) -> list[AuthorityContract]:
        return sorted(self._versions.get((tenant, contract_id), []), key=lambda c: c.version)

    def at(self, tenant: str, contract_id: str, *, as_of: datetime) -> AuthorityContract | None:
        """Which contract version was effective at a given time - the
        Phase 14 GRC answer to "which policy allowed this action on <date>"."""
        candidates = [c for c in self.history(tenant, contract_id)
                     if (c.effective_at or c.created_at) <= as_of]
        return max(candidates, key=lambda c: c.version) if candidates else None
