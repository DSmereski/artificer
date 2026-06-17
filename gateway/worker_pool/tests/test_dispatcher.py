"""Dispatcher CRUD + lifecycle tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.worker_pool.dispatcher import (
    Dispatcher,
    HiveJob,
    STATUS_DISPATCHED,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_FAILED,
    STATUS_QUEUED,
)


@pytest.fixture
def disp(tmp_path: Path) -> Dispatcher:
    return Dispatcher.open(tmp_path / "hive_jobs.db")


def test_enqueue_returns_job_with_id(disp: Dispatcher) -> None:
    job = disp.enqueue(
        kind="ollama.generate",
        payload={"model": "qwen2.5:1.5b", "prompt": "hi"},
        required_caps=("ollama",),
    )
    assert job.id.startswith("j_")
    assert job.kind == "ollama.generate"
    assert job.payload == {"model": "qwen2.5:1.5b", "prompt": "hi"}
    assert job.required_caps == ("ollama",)
    assert job.status == STATUS_QUEUED
    assert job.attempts == 0
    assert job.max_attempts == 3
    assert job.node_id is None
    assert job.created > 0


def test_enqueue_persists_across_reopen(tmp_path: Path) -> None:
    db = tmp_path / "hive_jobs.db"
    disp_a = Dispatcher.open(db)
    job = disp_a.enqueue(
        kind="ollama.generate", payload={"x": 1}, required_caps=("ollama",),
    )
    disp_b = Dispatcher.open(db)
    found = disp_b.get(job.id)
    assert found is not None
    assert found.id == job.id
    assert found.payload == {"x": 1}


def test_get_queued_oldest_first(disp: Dispatcher) -> None:
    a = disp.enqueue(kind="k", payload={"i": 1}, required_caps=())
    b = disp.enqueue(kind="k", payload={"i": 2}, required_caps=())
    c = disp.enqueue(kind="k", payload={"i": 3}, required_caps=())
    queued = disp.get_queued()
    ids = [j.id for j in queued]
    assert ids == [a.id, b.id, c.id]


def test_get_queued_excludes_done_and_failed(disp: Dispatcher) -> None:
    a = disp.enqueue(kind="k", payload={}, required_caps=())
    b = disp.enqueue(kind="k", payload={}, required_caps=())
    disp.assign_to_node(a.id, node_id="n_x")
    disp.complete(a.id, result={"ok": True}, duration_ms=10, node_id="n_x")
    queued_ids = {j.id for j in disp.get_queued()}
    assert a.id not in queued_ids
    assert b.id in queued_ids


def test_required_caps_roundtrip(disp: Dispatcher) -> None:
    job = disp.enqueue(
        kind="comfy.txt2img",
        payload={"prompt": "x"},
        required_caps=("comfy", "vram_mb>=8000"),
    )
    again = disp.get(job.id)
    assert again is not None
    assert again.required_caps == ("comfy", "vram_mb>=8000")


def test_assign_to_node_marks_dispatched(disp: Dispatcher) -> None:
    job = disp.enqueue(kind="k", payload={}, required_caps=())
    assert disp.assign_to_node(job.id, node_id="n_x") is True
    again = disp.get(job.id)
    assert again is not None
    assert again.status == STATUS_DISPATCHED
    assert again.node_id == "n_x"
    assert again.attempts == 1
    assert again.dispatched_at is not None


def test_assign_to_node_fails_if_already_dispatched(disp: Dispatcher) -> None:
    job = disp.enqueue(kind="k", payload={}, required_caps=())
    disp.assign_to_node(job.id, node_id="n_a")
    # Second assign should refuse (status no longer 'queued').
    assert disp.assign_to_node(job.id, node_id="n_b") is False
    again = disp.get(job.id)
    assert again is not None
    assert again.node_id == "n_a"


def test_complete_sets_done_and_result(disp: Dispatcher) -> None:
    job = disp.enqueue(kind="k", payload={}, required_caps=())
    disp.assign_to_node(job.id, node_id="n_x")
    assert disp.complete(job.id, result={"output": "hi"}, duration_ms=42, node_id="n_x") is True
    again = disp.get(job.id)
    assert again is not None
    assert again.status == STATUS_DONE
    assert again.result == {"output": "hi"}
    assert again.duration_ms == 42
    assert again.completed_at is not None


def test_complete_fails_if_not_dispatched(disp: Dispatcher) -> None:
    job = disp.enqueue(kind="k", payload={}, required_caps=())
    # Skip assign_to_node — job is still queued.
    assert disp.complete(job.id, result={}, duration_ms=1, node_id="n_x") is False


def test_fail_under_max_attempts_requeues(disp: Dispatcher) -> None:
    job = disp.enqueue(
        kind="k", payload={}, required_caps=(), max_attempts=3,
    )
    disp.assign_to_node(job.id, node_id="n_x")
    # First failure: attempts=1, status returns to queued.
    final = disp.fail(job.id, error="oops")
    again = disp.get(job.id)
    assert again is not None
    assert again.status == STATUS_QUEUED
    assert again.attempts == 1
    assert again.error == "oops"
    assert final is False  # not yet terminal


def test_fail_at_max_attempts_marks_failed(disp: Dispatcher) -> None:
    job = disp.enqueue(
        kind="k", payload={}, required_caps=(), max_attempts=2,
    )
    disp.assign_to_node(job.id, node_id="n_x")
    disp.fail(job.id, error="first")
    disp.assign_to_node(job.id, node_id="n_y")
    final = disp.fail(job.id, error="second")
    again = disp.get(job.id)
    assert again is not None
    assert again.status == STATUS_FAILED
    assert again.attempts == 2
    assert final is True  # terminal


def test_complete_with_error_status_marks_error(disp: Dispatcher) -> None:
    """Adapter-reported errors (status='error') count as terminal — they're
    distinct from infrastructure failures (status='failed' after retries)."""
    job = disp.enqueue(kind="k", payload={}, required_caps=())
    disp.assign_to_node(job.id, node_id="n_x")
    assert disp.report_adapter_error(
        job.id, error="model not found", duration_ms=5, node_id="n_x",
    ) is True
    again = disp.get(job.id)
    assert again is not None
    assert again.status == STATUS_ERROR
    assert again.error == "model not found"
    assert again.duration_ms == 5


def test_requeue_orphaned_returns_jobs_to_queue(disp: Dispatcher) -> None:
    """When a node disappears, in-flight jobs assigned to it must go
    back to `queued` (or `failed` if at max_attempts)."""
    a = disp.enqueue(
        kind="k", payload={"i": 1}, required_caps=(), max_attempts=3,
    )
    b = disp.enqueue(
        kind="k", payload={"i": 2}, required_caps=(), max_attempts=3,
    )
    c = disp.enqueue(
        kind="k", payload={"i": 3}, required_caps=(), max_attempts=3,
    )
    disp.assign_to_node(a.id, node_id="n_dead")
    disp.assign_to_node(b.id, node_id="n_dead")
    # c stays queued (never assigned).

    n_requeued, n_failed = disp.requeue_orphaned(node_id="n_dead")
    assert n_requeued == 2
    assert n_failed == 0

    again_a = disp.get(a.id)
    again_b = disp.get(b.id)
    assert again_a is not None and again_a.status == STATUS_QUEUED
    assert again_b is not None and again_b.status == STATUS_QUEUED
    # Attempts NOT incremented again here — assign_to_node already bumped
    # them on the first dispatch. requeue_orphaned just resets node_id.
    assert again_a.attempts == 1
    assert again_a.node_id is None


def test_requeue_orphaned_fails_when_at_max_attempts(disp: Dispatcher) -> None:
    job = disp.enqueue(
        kind="k", payload={}, required_caps=(), max_attempts=1,
    )
    disp.assign_to_node(job.id, node_id="n_dead")
    # attempts is now 1, == max_attempts — orphan recovery must mark failed.
    n_requeued, n_failed = disp.requeue_orphaned(node_id="n_dead")
    assert n_requeued == 0
    assert n_failed == 1
    again = disp.get(job.id)
    assert again is not None
    assert again.status == STATUS_FAILED
    assert again.error and "node disappeared" in again.error.lower()


def test_requeue_orphaned_only_touches_dispatched(disp: Dispatcher) -> None:
    a = disp.enqueue(kind="k", payload={}, required_caps=())
    b = disp.enqueue(kind="k", payload={}, required_caps=())
    disp.assign_to_node(a.id, node_id="n_dead")
    disp.complete(a.id, result={"ok": True}, duration_ms=1, node_id="n_dead")
    disp.assign_to_node(b.id, node_id="n_alive")
    n_requeued, n_failed = disp.requeue_orphaned(node_id="n_dead")
    assert n_requeued == 0  # a is done, b is on a different node
    assert n_failed == 0
    # b untouched
    again = disp.get(b.id)
    assert again is not None and again.status == STATUS_DISPATCHED
