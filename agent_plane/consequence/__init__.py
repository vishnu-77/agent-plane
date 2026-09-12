"""Consequence modelling: what an authorised action can actually cause.

``deployment.restart`` against a development sandbox, a staging workload,
and a production payment service is the same string three times. The
consequence is not. This package turns (action, resource) into a structured
:class:`Consequence` from an operator-declared resource catalog, so that a
lease can bound *effects*, not just verbs.
"""
from agent_plane.consequence.catalog import (
    IMPACT_RANK,
    ActionProfile,
    Consequence,
    ConsequenceCatalog,
    ResourceProfile,
    build_consequence_catalog,
)

__all__ = [
    "IMPACT_RANK",
    "ActionProfile",
    "Consequence",
    "ConsequenceCatalog",
    "ResourceProfile",
    "build_consequence_catalog",
]
