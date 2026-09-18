"""Identity assurance ladder. See spec/identity-assurance.md."""
from __future__ import annotations

from enum import Enum


class IdentityAssurance(str, Enum):
    """Ordered, structural - not a numeric trust score. A level is either
    met or it isn't, and the reason is always inspectable."""

    SELF_ASSERTED = "self_asserted"
    API_KEY_BOUND = "api_key_bound"
    RUNTIME_CREDENTIAL = "runtime_credential"
    VERIFIED_DELEGATION = "verified_delegation"
