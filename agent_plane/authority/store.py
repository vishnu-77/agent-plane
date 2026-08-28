"""Authority Lease store.

ponytail: in-memory, single-process (same tradeoff as the runtime revocation
set in ``main.py``) - leases and usage counters don't survive a restart or
share across workers. Move to the SQL-backed pattern used by
``AuditStore``/``UsageStore`` if leases need to persist or scale out.
"""
from __future__ import annotations

import threading
from pathlib import Path

import yaml

from agent_plane.authority.lease import AuthorityLease, parse_lease
from agent_plane.config import Settings

_DEFAULT_LEASES_FILE = "config/leases.yaml"


class LeaseStore:
    def __init__(self, leases: list[AuthorityLease] | None = None):
        self._leases: dict[str, AuthorityLease] = {lease.id: lease for lease in (leases or [])}
        self._usage: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def add(self, lease: AuthorityLease) -> None:
        with self._lock:
            self._leases[lease.id] = lease

    def get(self, lease_id: str) -> AuthorityLease | None:
        return self._leases.get(lease_id)

    def list(self) -> list[AuthorityLease]:
        return list(self._leases.values())

    def for_subject_task(self, subject: str, task: str) -> list[AuthorityLease]:
        return [
            lease for lease in self._leases.values()
            if lease.subject == subject and lease.task == task
        ]

    def use_count(self, lease_id: str, action: str) -> int:
        return self._usage.get((lease_id, action), 0)

    def try_consume(self, lease_id: str, action: str, limit: int | None) -> bool:
        """Atomically check-and-increment a per-lease/action usage counter.

        Returns False (without incrementing) once ``limit`` is reached.
        """
        with self._lock:
            key = (lease_id, action)
            count = self._usage.get(key, 0)
            if limit is not None and count >= limit:
                return False
            self._usage[key] = count + 1
            return True


def load_leases(path: str) -> list[AuthorityLease]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [parse_lease(d) for d in doc.get("leases", [])]


def build_lease_store(settings: Settings) -> LeaseStore:
    path: str | None = settings.leases_file or (
        _DEFAULT_LEASES_FILE if Path(_DEFAULT_LEASES_FILE).exists() else None
    )
    if path is None:
        from agent_plane.defaults import default_config_file

        default = default_config_file("leases.yaml")
        path = str(default) if default.exists() else None
    return LeaseStore(load_leases(path) if path else [])
