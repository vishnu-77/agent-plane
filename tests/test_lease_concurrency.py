"""LeaseStore.try_consume must not over-consume a max_uses-capped lease under
concurrent callers (mirrors the audit store's own concurrency test - this is
the one piece of authority state that had no concurrency coverage)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from agent_plane.authority.lease import AuthorityLease
from agent_plane.authority.store import LeaseStore


def test_concurrent_try_consume_never_exceeds_the_limit():
    store = LeaseStore([
        AuthorityLease(id="l1", task="t1", subject="a1", resources=["*"], actions=["x"])
    ])
    limit = 20
    attempts = 200

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: store.try_consume("l1", "x", limit), range(attempts)))

    assert results.count(True) == limit
    assert store.use_count("l1", "x") == limit
