"""PrincipalIdentity. See spec/principals.md.

Deliberately thin on verification machinery: no issuer/JWKS handling here.
Build that out only once a real caller (e.g. workload attestation) proves
what's actually needed.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from agent_plane.identity.assurance import IdentityAssurance


class PrincipalIdentity(BaseModel):
    principal_id: str
    principal_type: Literal["human", "agent", "service", "workload"] = "agent"
    subject: str | None = None
    issuer: str | None = None
    trust_domain: str | None = None
    assurance: IdentityAssurance = IdentityAssurance.REPORTED
    # Who is responsible for this principal (see spec/principals.md's
    # ownership note) - a human, team, or service id. Unset = unowned.
    owner_principal: str | None = None
    # For principal_type="workload": the cloud/orchestrator workload
    # identifier this principal is bound to (e.g. a SPIFFE ID or instance id).
    workload_id: str | None = None
    # How this principal was authenticated (e.g. "project_api_key",
    # "runtime_credential", "delegation_jwt"). Free text, not an enum -
    # new authentication methods shouldn't require a schema change.
    authentication_method: str | None = None
