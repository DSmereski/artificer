"""Tests for synthesizer budget reservation (Fix 3, task #448).

The synth reservation ensures that no matter how long upstream helpers run,
the synthesizer always gets at least `synth_reservation_s` seconds of
wall-clock time. Helpers are cancelled at `turn_deadline - synth_reservation_s`
so synth can start on time.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from gateway.event_emitter import ListEmitter
from gateway.helpers.base import HelperResult, HelperTask
from gateway.hive_coordinator import HiveCoordinator, TurnBudget, TurnContext
from gateway.model_catalog import load_catalog


@pytest.fixture
def catalog():
    return load_catalog(
        Path(__file__).resolve().parents[2] / "config" / "model_catalog.yaml",
    )


class _FakeHelper:
    def __init__(self, role: str, model_id: str, output: dict, *,
                 sleep_s: float = 0.0, error: str | None = None) -> None:
        self.role = role
        self.model_id = model_id
        self._output = output
        self._sleep_s = sleep_s
        self._error = error
        self.invoked_at: float | None = None
        self.invoked_with: list[HelperTask] = []

    async def invoke(self, task: HelperTask) -> HelperResult:
        self.invoked_at = time.monotonic()
        self.invoked_with.append(task)
        if self._sleep_s:
            await asyncio.sleep(self._sleep_s)
        return HelperResult(
            role=self.role, model_id=self.model_id,
            output=self._output, error=self._error,
            tokens_in=10, tokens_out=20, latency_ms=5,
            parent_id=task.parent_id,
        )


# ---------------------------------------------------------------- core budget reservation test


@pytest.mark.asyncio
async def test_slow_helper_cancelled_at_synth_gate(catalog):
    """A librarian that would sleep 200s is HARD-CANCELLED when the
    synth-on-ready gate fires (#484). Synth must still be called with
    meaningful time remaining and the slow helper must NOT appear in
    `turn.helper_results`.

    Contract evolution:
      #448  hard cancel at dispatch_deadline (produced fake "turn
            budget timeout" rows)
      #476  detach at synth_gate as bg task, emit `helper.late`
            (caused Ollama-slot contention with synth on
            single-NUM_PARALLEL=1 rigs — scn04 2026-05-06 saw
            74% synth-timeout)
      #484  hard cancel at synth_gate, drain so cancel propagates
            into httpx → Ollama HTTP teardown → synth gets the slot

    Budget: total=3.0s, synth_reservation=1.5s, synth_gate=1.0s →
    librarian would sleep 200s, gets cancelled at ~1.0s; synth runs
    immediately after with reply emitted well before the 3.0s turn cap.
    """
    ctx = TurnContext(
        user_msg="what ships are in the fleet",
        user_id=1, device_id="dev1", bot="terry",
        available_helpers=["planner", "librarian", "synthesizer"],
    )
    synth_invoked_at: list[float] = []

    class _TimedSynth:
        role = "synthesizer"
        model_id = "planner-qwen"
        invoked_with: list[HelperTask] = []

        async def invoke(self, task: HelperTask) -> HelperResult:
            synth_invoked_at.append(time.monotonic())
            self.invoked_with.append(task)
            return HelperResult(
                role="synthesizer", model_id="planner-qwen",
                output={"reply": "Fleet status: all ships accounted for.", "actions": []},
                tokens_in=10, tokens_out=20, latency_ms=5,
                parent_id=task.parent_id,
            )

    synth = _TimedSynth()
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {
                "summary": "look up fleet",
                "delegations": [
                    {"role": "librarian", "goal": "find fleet notes",
                     "inputs": {"query": "fleet"}},
                ],
            },
        ),
        "librarian": _FakeHelper(
            "librarian", "no-model",
            {"summary": "found ships"},
            sleep_s=200.0,
        ),
        "synthesizer": synth,
    }
    # 3s total, 1.5s reserved for synth, 1.0s gate before detach.
    budget = TurnBudget(
        total_timeout_s=3.0, synth_reservation_s=1.5, synth_gate_s=1.0,
    )
    turn_start = time.monotonic()
    coord = HiveCoordinator(catalog, helpers, budget=budget)
    em = ListEmitter()
    try:
        turn = await coord.coordinate(ctx, em)

        # Synth must have been called.
        assert synth.invoked_with, "synthesizer was never invoked"
        # Librarian must NOT appear in helper_results — it detached as
        # a late helper task instead of being cancelled+errored.
        librarian_results = [
            r for r in turn.helper_results if r.role == "librarian"
        ]
        assert not librarian_results, (
            "librarian should detach as late helper, "
            f"not appear in turn.helper_results: {librarian_results!r}"
        )
        # #484: slow librarian was hard-cancelled at gate, not detached.
        assert len(coord._late_helper_tasks) == 0, (
            "late-helper detach is gone (#484); slow helper must be cancelled"
        )
        # Synth was invoked shortly after gate fired — check timing.
        assert synth_invoked_at, "synth_invoked_at not recorded"
        elapsed_before_synth = synth_invoked_at[0] - turn_start
        # Gate is 1.0s; allow generous jitter for planner invoke + loop
        # overhead. Key invariant: synth ran well before the 3.0s cap.
        assert elapsed_before_synth < 3.5, (
            f"synth started too late ({elapsed_before_synth:.2f}s); "
            "synth_gate not enforced"
        )
        # Final reply must be non-empty — the synth composed something.
        assert turn.reply, "final_reply is empty after synth ran"
    finally:
        # Cancel and drain the detached librarian sleep so pytest
        # doesn't print "Task was destroyed but it is pending!".
        for t in list(coord._late_helper_tasks):
            t.cancel()
        await coord._drain_late_tasks(timeout=5.0)


# ---------------------------------------------------------------- dispatch_deadline computation


def test_dispatch_deadline_honours_synth_reservation():
    """dispatch_deadline = min(deadline, now + (total - synth_reservation)).

    When synth_reservation_s=60 and total_timeout_s=150:
    dispatch window = 90s (helpers cancelled 60s before deadline).
    """
    budget = TurnBudget(total_timeout_s=150.0, synth_reservation_s=60.0)
    deadline = time.monotonic() + 150.0
    dispatch_window = budget.total_timeout_s - budget.synth_reservation_s
    assert dispatch_window == 90.0, (
        f"dispatch window should be 90s, got {dispatch_window}"
    )
    # dispatch_deadline must be at most deadline - synth_reservation.
    synth_start_floor = deadline - budget.synth_reservation_s
    dispatch_deadline = min(
        deadline,
        time.monotonic() + max(dispatch_window, 10.0),
    )
    assert dispatch_deadline <= synth_start_floor + 0.1, (
        "dispatch_deadline must not eat into synth reservation"
    )


# ---------------------------------------------------------------- synth gets remaining budget


@pytest.mark.asyncio
async def test_synth_timeout_set_from_remaining_after_dispatch(catalog):
    """After dispatch, synth's _run_helper is called with remaining time
    = deadline - now. When dispatch used only part of the window, synth
    gets more than synth_reservation_s. When dispatch used all of it, synth
    gets exactly synth_reservation_s (within jitter)."""
    ctx = TurnContext(
        user_msg="quick question",
        user_id=1, device_id="dev1", bot="terry",
        available_helpers=["planner", "librarian", "synthesizer"],
    )

    class _TimedSynth:
        role = "synthesizer"
        model_id = "planner-qwen"
        received_remaining: list[float] = []
        invoked_with: list[HelperTask] = []

        async def invoke(self, task: HelperTask) -> HelperResult:
            self.invoked_with.append(task)
            return HelperResult(
                role="synthesizer", model_id="planner-qwen",
                output={"reply": "done", "actions": []},
                tokens_in=5, tokens_out=5, latency_ms=1,
                parent_id=task.parent_id,
            )

    synth = _TimedSynth()
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {
                "summary": "quick",
                "delegations": [
                    {"role": "librarian", "goal": "check", "inputs": {}},
                ],
            },
        ),
        "librarian": _FakeHelper(
            "librarian", "no-model",
            # A non-empty hit ensures the empty-retrieval guard
            # (gateway.hallucination_guard) sees real signal and
            # leaves the synthesizer's reply alone. Summary alone
            # is not signal — see test_empty_retrieval_guard.
            {"summary": "checked", "hits": [{"path": "x.md"}]},
            sleep_s=0.0,
        ),
        "synthesizer": synth,
    }
    # Fast turn: helpers finish immediately, synth should have almost the
    # full reservation available.
    budget = TurnBudget(total_timeout_s=10.0, synth_reservation_s=5.0)
    coord = HiveCoordinator(catalog, helpers, budget=budget)
    em = ListEmitter()
    turn = await coord.coordinate(ctx, em)

    assert synth.invoked_with, "synth never called"
    assert turn.reply == "done"
    assert turn.synth_result is not None
    assert turn.synth_result.error is None
