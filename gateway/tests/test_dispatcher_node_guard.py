"""Unit tests for node_id guard in complete() and report_adapter_error() (Fix 3).

Both methods must reject calls where the node_id does not match the row's
node_id, even if status=dispatched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.worker_pool.dispatcher import (
    Dispatcher,
    STATUS_DISPATCHED,
    STATUS_DONE,
    STATUS_ERROR,
)


@pytest.fixture
def disp(tmp_path: Path) -> Dispatcher:
    return Dispatcher.open(tmp_path / "jobs.db")


def _enqueue_and_dispatch(disp: Dispatcher, node_id: str = "n1") -> str:
    job = disp.enqueue(kind="test", payload={}, max_attempts=3)
    ok = disp.assign_to_node(job.id, node_id=node_id)
    assert ok
    return job.id


# ---------------------------------------------------------------------------
# complete() — wrong node_id is a no-op
# ---------------------------------------------------------------------------

def test_complete_wrong_node_id_returns_false(disp: Dispatcher) -> None:
    jid = _enqueue_and_dispatch(disp, node_id="n1")

    ok = disp.complete(jid, result={"x": 1}, duration_ms=10, node_id="wrong-node")

    assert ok is False, "complete() with wrong node_id should return False"
    assert disp.get(jid).status == STATUS_DISPATCHED, "status should be unchanged"


def test_complete_correct_node_id_works(disp: Dispatcher) -> None:
    jid = _enqueue_and_dispatch(disp, node_id="n1")

    ok = disp.complete(jid, result={"x": 1}, duration_ms=10, node_id="n1")

    assert ok is True
    assert disp.get(jid).status == STATUS_DONE


# ---------------------------------------------------------------------------
# report_adapter_error() — wrong node_id is a no-op
# ---------------------------------------------------------------------------

def test_report_adapter_error_wrong_node_id_returns_false(disp: Dispatcher) -> None:
    jid = _enqueue_and_dispatch(disp, node_id="n1")

    ok = disp.report_adapter_error(
        jid, error="crash", duration_ms=5, node_id="wrong-node",
    )

    assert ok is False, "report_adapter_error() with wrong node_id should return False"
    assert disp.get(jid).status == STATUS_DISPATCHED, "status should be unchanged"


def test_report_adapter_error_correct_node_id_works(disp: Dispatcher) -> None:
    jid = _enqueue_and_dispatch(disp, node_id="n1")

    ok = disp.report_adapter_error(
        jid, error="model crash", duration_ms=5, node_id="n1",
    )

    assert ok is True
    assert disp.get(jid).status == STATUS_ERROR
