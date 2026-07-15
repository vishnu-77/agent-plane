"""The signed audit chain must stay single + valid under concurrent writers."""
from __future__ import annotations

import threading

from agent_plane.audit.signing import verify_chain
from agent_plane.audit.store import SqlAuditStore

KEY = "concurrency-key"


def _event(tag: str) -> dict:
    # Minimal valid AuditEvent payload (non-null columns).
    return {
        "decision_id": f"dec_{tag}",
        "user_id": "u",
        "tenant": "t",
        "model_requested": "m",
        "data_classification": "public",
        "decision": "allow",
    }


def test_concurrent_writes_keep_one_valid_chain(tmp_path):
    store = SqlAuditStore(f"sqlite:///{tmp_path / 'audit.db'}", KEY)
    threads_n, per_thread = 4, 25
    errors: list[Exception] = []

    def worker(w: int) -> None:
        try:
            for i in range(per_thread):
                store.record(_event(f"{w}-{i}"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    events = list(reversed(store.recent(limit=10_000)))  # chronological order
    assert len(events) == threads_n * per_thread
    # A fork would break verification; a single unbroken chain verifies.
    assert verify_chain(events, KEY) is True
    # Every link is unique (no duplicate appends).
    assert len({e["event_hash"] for e in events}) == len(events)
