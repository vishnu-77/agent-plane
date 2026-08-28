"""The task-authority primitive: capability != authority.

An ``Actor`` (agent_plane.schemas.canonical) already carries *capability* -
what it can generically do (``allowed_tools``, clearance). This package adds
the missing dimension: what a specific *task* authorises it to do, right now,
against which resources (:class:`agent_plane.authority.lease.AuthorityLease`).
See ``spec/authority-lease.md``.
"""
