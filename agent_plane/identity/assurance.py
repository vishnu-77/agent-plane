"""Identity assurance ladder. See spec/identity-assurance.md."""
from __future__ import annotations

from enum import Enum


class IdentityAssurance(str, Enum):
    """Ordered, structural - not a numeric trust score. A level is either
    met or it isn't, and the reason is always inspectable."""

    REPORTED = "reported"
    CONNECTOR_AUTHENTICATED = "connector_authenticated"
    DELEGATED_VERIFIED = "delegated_verified"
    WORKLOAD_ATTESTED = "workload_attested"


# Lowest -> highest, mirrors agent_plane.authority.lease.IMPACT_RANK's
# convention. Used to compare a lease's min_assurance floor (or a parent
# lease's floor during delegation attenuation) against what was actually
# established. An unrecognized/missing level ranks below REPORTED (fail
# closed), matching IMPACT_RANK's "unknown ranks worst" convention.
ASSURANCE_RANK: dict[str, int] = {
    IdentityAssurance.REPORTED: 0,
    IdentityAssurance.CONNECTOR_AUTHENTICATED: 1,
    IdentityAssurance.DELEGATED_VERIFIED: 2,
    IdentityAssurance.WORKLOAD_ATTESTED: 3,
}
