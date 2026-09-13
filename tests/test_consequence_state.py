"""Task-scoped consequence state: proposed/confirmed facts, CAS'd by revision."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_plane.consequence.state import SqlTaskConsequenceStateStore, TaskFact


@pytest.fixture()
def store(tmp_path):
    return SqlTaskConsequenceStateStore(f"sqlite:///{tmp_path / 'consequence_state.db'}")


def test_empty_task_has_no_facts(store):
    state = store.get("proj", "task-1")
    assert state.facts == []
    assert state.revision == 0
    assert state.confirmed_kinds() == frozenset()


def test_propose_then_confirm(store):
    store.propose("proj", "task-1", TaskFact(kind="workflow_definition", resource="workspace/.github/workflows/deploy.yml",
                                             created_by_decision="dec-1"))
    state = store.get("proj", "task-1")
    assert state.revision == 1
    assert state.proposed_kinds() == {"workflow_definition"}
    assert state.confirmed_kinds() == frozenset()  # proposed only, not confirmed yet

    changed = store.confirm("proj", "task-1", "dec-1")
    assert changed == 1
    state = store.get("proj", "task-1")
    assert state.confirmed_kinds() == {"workflow_definition"}
    assert state.revision == 2

    # Confirming again changes nothing - it's already confirmed, not proposed.
    assert store.confirm("proj", "task-1", "dec-1") == 0


def test_facts_are_scoped_per_task(store):
    store.propose("proj", "task-1", TaskFact(kind="workflow_definition", resource="r", created_by_decision="d1"))
    store.propose("proj", "task-2", TaskFact(kind="secret_resource", resource="r2", created_by_decision="d2"))
    assert store.get("proj", "task-1").proposed_kinds() == {"workflow_definition"}
    assert store.get("proj", "task-2").proposed_kinds() == {"secret_resource"}


def test_confirming_an_unknown_decision_changes_nothing(store):
    store.propose("proj", "task-1", TaskFact(kind="workflow_definition", resource="r", created_by_decision="d1"))
    assert store.confirm("proj", "task-1", "some-other-decision") == 0
    assert store.get("proj", "task-1").confirmed_kinds() == frozenset()


def test_concurrent_propose_never_drops_a_fact(store):
    """Two sub-agents proposing facts for the same task at once must both
    land - a plain read-then-overwrite (no revision guard) would let the
    second writer's commit silently clobber the first's."""
    attempts = 30

    def propose(i: int) -> None:
        store.propose("proj", "shared-task",
                      TaskFact(kind="repository_state", resource=f"r{i}", created_by_decision=f"d{i}"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(propose, range(attempts)))

    state = store.get("proj", "shared-task")
    assert len(state.facts) == attempts
    assert {f.created_by_decision for f in state.facts} == {f"d{i}" for i in range(attempts)}
