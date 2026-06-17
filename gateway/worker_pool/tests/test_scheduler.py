"""Scheduler matches queued jobs to a polling node's capability set."""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.worker_pool.dispatcher import Dispatcher, STATUS_DISPATCHED
from gateway.worker_pool.scheduler import NodeView, Scheduler


@pytest.fixture
def disp(tmp_path: Path) -> Dispatcher:
    return Dispatcher.open(tmp_path / "hive_jobs.db")


@pytest.fixture
def sched(disp: Dispatcher) -> Scheduler:
    return Scheduler(dispatcher=disp)


def _node(
    *, caps: tuple[str, ...] = (), vram_mb: int = 0, node_id: str = "n_x",
) -> NodeView:
    return NodeView(node_id=node_id, caps=set(caps), vram_free_mb=vram_mb)


def test_pick_returns_oldest_matching_job(sched: Scheduler, disp: Dispatcher) -> None:
    a = disp.enqueue(kind="ollama.generate", payload={"i": 1}, required_caps=("ollama",))
    b = disp.enqueue(kind="ollama.generate", payload={"i": 2}, required_caps=("ollama",))
    picked = sched.pick_for_node(_node(caps=("ollama",), vram_mb=20000))
    assert picked is not None
    assert picked.id == a.id
    # And pick again returns b (a is now dispatched).
    picked2 = sched.pick_for_node(_node(caps=("ollama",), vram_mb=20000))
    assert picked2 is not None and picked2.id == b.id


def test_pick_skips_jobs_requiring_missing_caps(
    sched: Scheduler, disp: Dispatcher,
) -> None:
    disp.enqueue(kind="comfy.txt2img", payload={}, required_caps=("comfy",))
    b = disp.enqueue(kind="ollama.generate", payload={}, required_caps=("ollama",))
    picked = sched.pick_for_node(_node(caps=("ollama",), vram_mb=20000))
    assert picked is not None and picked.id == b.id


def test_pick_returns_none_when_no_match(
    sched: Scheduler, disp: Dispatcher,
) -> None:
    disp.enqueue(kind="comfy.txt2img", payload={}, required_caps=("comfy",))
    assert sched.pick_for_node(_node(caps=("ollama",), vram_mb=20000)) is None


def test_pick_respects_vram_floor(sched: Scheduler, disp: Dispatcher) -> None:
    disp.enqueue(
        kind="comfy.txt2img", payload={},
        required_caps=("comfy", "vram_mb>=8000"),
    )
    too_small = sched.pick_for_node(_node(caps=("comfy",), vram_mb=2000))
    assert too_small is None
    big_enough = sched.pick_for_node(_node(caps=("comfy",), vram_mb=12000))
    assert big_enough is not None


def test_pick_marks_job_dispatched(sched: Scheduler, disp: Dispatcher) -> None:
    job = disp.enqueue(kind="k", payload={}, required_caps=())
    picked = sched.pick_for_node(_node(node_id="n_alpha"))
    assert picked is not None and picked.id == job.id
    again = disp.get(job.id)
    assert again is not None
    assert again.status == STATUS_DISPATCHED
    assert again.node_id == "n_alpha"


def test_pick_skips_already_dispatched_job_under_race(
    sched: Scheduler, disp: Dispatcher,
) -> None:
    """Two nodes poll at the same time; only one gets the job."""
    job = disp.enqueue(kind="k", payload={}, required_caps=())
    first = sched.pick_for_node(_node(node_id="n_a"))
    second = sched.pick_for_node(_node(node_id="n_b"))
    assert first is not None and first.id == job.id
    assert second is None  # already taken
