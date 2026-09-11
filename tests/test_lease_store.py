"""Durable lease store: state survives a restart and is shared across store
instances (i.e. replicas) pointed at the same database."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from agent_plane.authority.lease import AuthorityLease
from agent_plane.authority.store import LeaseStore, SqlLeaseStore


def _lease(**overrides) -> AuthorityLease:
    base = dict(id="lease-a", task="task", subject="agent", resources=["r/*"],
                actions=["x.do"], max_uses={"x.do": 3})
    base.update(overrides)
    return AuthorityLease(**base)


@pytest.fixture()
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'authority.db'}"


def test_runtime_issued_lease_survives_restart(db_url):
    first = SqlLeaseStore(db_url)
    first.add(_lease())
    assert first.try_consume("lease-a", "x.do", 3)

    second = SqlLeaseStore(db_url)  # "restart" / second replica
    assert second.get("lease-a") is not None
    assert second.use_count("lease-a", "x.do") == 1


def test_revocation_is_visible_to_other_replicas(db_url):
    a, b = SqlLeaseStore(db_url), SqlLeaseStore(db_url)
    a.add(_lease())
    assert a.revoke("lease-a")
    assert b.get("lease-a").revoked is True
    assert a.revoke("missing") is False


def test_seed_does_not_overwrite_stored_state(db_url):
    store = SqlLeaseStore(db_url)
    store.seed([_lease()])
    store.revoke("lease-a")
    inserted = SqlLeaseStore(db_url).seed([_lease()])  # same YAML on restart
    assert inserted == 0
    assert SqlLeaseStore(db_url).get("lease-a").revoked is True


def test_use_reservation_is_atomic_across_replicas(db_url):
    SqlLeaseStore(db_url).add(_lease(max_uses={"x.do": 5}))
    replicas = [SqlLeaseStore(db_url) for _ in range(4)]

    def attempt(i: int) -> bool:
        return replicas[i % 4].try_consume("lease-a", "x.do", 5)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(40)))
    assert sum(results) == 5
    assert replicas[0].use_count("lease-a", "x.do") == 5


def test_unlimited_actions_count_but_never_refuse(db_url):
    store = SqlLeaseStore(db_url)
    store.add(_lease(max_uses={}))
    assert all(store.try_consume("lease-a", "x.do", None) for _ in range(10))
    assert store.use_count("lease-a", "x.do") == 10


def test_for_subject_task_and_list(db_url):
    store = SqlLeaseStore(db_url)
    store.add(_lease(id="l1"))
    store.add(_lease(id="l2", task="other"))
    store.add(_lease(id="l3", subject="someone-else"))
    assert {lease.id for lease in store.for_subject_task("agent", "task")} == {"l1"}
    assert {lease.id for lease in store.list()} == {"l1", "l2", "l3"}


def test_shrink_persists_via_add(db_url):
    store = SqlLeaseStore(db_url)
    store.add(_lease(actions=["x.do", "x.undo"]))
    store.add(store.get("lease-a").model_copy(update={"actions": ["x.do"]}))
    assert SqlLeaseStore(db_url).get("lease-a").actions == ["x.do"]


def test_request_ledger_is_shared_and_conflict_safe(db_url):
    a, b = SqlLeaseStore(db_url), SqlLeaseStore(db_url)
    key = ("acme", "worker", "req-1")
    a.request_reserve(key, "digest", capacity=100)
    assert b.request_lookup(key) == ("digest", None)
    with pytest.raises(ValueError):
        b.request_reserve(key, "digest", capacity=100)  # already in progress elsewhere
    a.request_complete(key, "digest", {"decision": "allow"})
    assert b.request_lookup(key) == ("digest", {"decision": "allow"})
    assert a.purge_requests(datetime.now(UTC) + timedelta(seconds=1)) == 1
    assert b.request_lookup(key) is None


def test_request_ledger_capacity(db_url):
    store = SqlLeaseStore(db_url)
    store.request_reserve(("t", "a", "k1"), "d", capacity=1)
    with pytest.raises(ValueError):
        store.request_reserve(("t", "a", "k2"), "d", capacity=1)


def test_memory_store_ledger_matches_interface():
    store = LeaseStore()
    key = ("t", "a", "k")
    assert store.request_lookup(key) is None
    store.request_reserve(key, "d", capacity=10)
    assert store.request_lookup(key) == ("d", None)
    store.request_complete(key, "d", {"ok": True})
    assert store.request_lookup(key) == ("d", {"ok": True})


def test_transaction_is_reentrant(db_url):
    store = SqlLeaseStore(db_url)
    store.add(_lease())
    with store.transaction():
        with store.transaction():
            assert store.get("lease-a") is not None
