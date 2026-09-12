"""Runnable examples and smoke checks.

This file exists so ``examples`` is a real package: without it the directory is
only a namespace portion, and any installed distribution that happens to ship a
top-level ``examples`` package shadows it, breaking ``from examples... import``
in the tests. Packaging only ships ``agent_plane*``, so this is not distributed.
"""
