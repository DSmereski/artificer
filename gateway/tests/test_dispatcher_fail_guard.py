"""Unit tests for Dispatcher.fail() — must only act on STATUS_DISPATCHED rows.

Fix 1 guard: calling fail() on a done/failed/queued job must be a no-op,
so a heartbeat-miss sweep racing a completed job cannot corrupt state.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gateway.worker_pool.dispatcher import (
    Dispatcher,
    STATUS_DISPATCHED,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_FAILED,
    STATUS_QUEUED,
)


@pytest.fixture
def disp(tmp_path: Path) -> Dispatcher:
    return Dispatcher.open(tmp_path / "jobs.db")


def _enqueue_and_dispatch(disp: Dispatcher, node_id: str = "n1") -> str:
    """Return job_id for a job that has been dispatched to node_id."""
    job = disp.enqueue(kind="test", payload={}, max_attempts=3)
    ok = disp.assign_to_node(job.id, node_id=node_id)
    assert ok, "assign_to_node should succeed"
    return job.id


# ---------------------------------------------------------------------------
# No-op cases — fail() must leave status unchanged
# ---------------------------------------------------------------------------

def test_fail_on_done_job_is_noop(disp: Dispatcher) -> None:
    jid = _enqueue_and_dispatch(disp, node_id="n1")
    disp.complete(jid, result={}, duration_ms=1, node_id="n1")
    assert disp.get(jid).status == STATUS_DONE

    returned = disp.fail(jid, error="late heartbeat sweep")

    job = disp.get(jid)
    assert job.status == STATUS_DONE, "fail() must not overwrite a done job"
    assert returned is False, "fail() should return False (no-op)"


def test_fail_on_error_job_is_noop(disp: Dispatcher) -> None:
    jid = _enqueue_and_dispatch(disp, node_id="n1")
    disp.report_adapter_error(jid, error="model crash", duration_ms=1, node_id="n1")
    assert disp.get(jid).status == STATUS_ERROR

    returned = disp.fail(jid, error="late heartbeat sweep")

    job = disp.get(jid)
    assert job.status == STATUS_ERROR, "fail() must not overwrite an error job"
    assert returned is False


def test_fail_on_failed_job_is_noop(disp: Dispatcher) -> None:
    # max_attempts=1 → dispatch + fail → terminal 'failed'
    job = disp.enqueue(kind="test", payload={}, max_attempts=1)
    disp.assign_to_node(job.id, node_id="n1")
    disp.fail(job.id, error="first failure")  # this should mark it 'failed'
    assert disp.get(job.id).status == STATUS_FAILED

    returned = disp.fail(job.id, error="second sweep hit same job")

    assert disp.get(job.id).status == STATUS_FAILED, "fail() must not overwrite a failed job"
    assert returned is False


def test_fail_on_queued_job_is_noop(disp: Dispatcher) -> None:
    job = disp.enqueue(kind="test", payload={}, max_attempts=3)
    assert disp.get(job.id).status == STATUS_QUEUED

    returned = disp.fail(job.id, error="should be ignored")

    job_after = disp.get(job.id)
    assert job_after.status == STATUS_QUEUED, "fail() on queued must be a no-op"
    assert returned is False


# ---------------------------------------------------------------------------
# Active cases — fail() on a dispatched job
# ---------------------------------------------------------------------------

def test_fail_on_dispatched_job_under_cap_requeues(disp: Dispatcher) -> None:
    jid = _enqueue_and_dispatch(disp)  # max_attempts=3, attempts=1 after dispatch

    returned = disp.fail(jid, error="timeout")

    job = disp.get(jid)
    assert job.status == STATUS_QUEUED, "under cap → should requeue"
    assert job.node_id is None
    assert returned is False, "not terminal → returns False"


def test_fail_on_dispatched_job_at_cap_marks_failed(disp: Dispatcher) -> None:
    job = disp.enqueue(kind="test", payload={}, max_attempts=1)
    disp.assign_to_node(job.id, node_id="n1")  # attempts becomes 1 = max_attempts

    returned = disp.fail(job.id, error="final timeout")

    after = disp.get(job.id)
    assert after.status == STATUS_FAILED, "at cap → should be terminal failed"
    assert returned is True, "terminal → returns True"
