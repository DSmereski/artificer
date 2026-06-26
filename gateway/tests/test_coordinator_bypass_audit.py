"""Integration tests for coordinator-bypass audit (Fix 2, task #448).

Asserts that turns involving librarian and skill_runner delegations always
reach the synthesizer (synth_result is set, final_reply is non-empty), OR
are explicitly tagged with a design-bypass synth_mode.

The 20 production coordinator-bypass turns had delegations to librarian,
summarizer, skill_runner, and coder where the coordinator short-circuited.
These tests lock in the invariant that this cannot happen silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.event_emitter import ListEmitter
from gateway.helpers.base import HelperResult, HelperTask
from gateway.hive_coordinator import HiveCoordinator, TurnBudget, TurnContext
from gateway.model_catalog import load_catalog


class _FakeHelper:
    def __init__(self, role: str, model_id: str, output: dict, *,
                 error: str | None = None) -> None:
        self.role = role
        self.model_id = model_id
        self._output = output
        self._error = error
        self.invoked_with: list[HelperTask] = []

    async def invoke(self, task: HelperTask) -> HelperResult:
        self.invoked_with.append(task)
        return HelperResult(
            role=self.role, model_id=self.model_id,
            output=self._output, error=self._error,
            tokens_in=10, tokens_out=20, latency_ms=5,
            parent_id=task.parent_id,
        )


@pytest.fixture
def catalog():
    return load_catalog(
        Path(__file__).resolve().parents[2] / "config" / "model_catalog.yaml",
    )


# ---------------------------------------------------------------- librarian path


@pytest.mark.asyncio
async def test_librarian_delegation_reaches_synthesizer(catalog):
    """Librarian result must be passed to the synthesizer, not returned
    directly as final_reply. Catches coordinator-bypass for librarian."""
    ctx = TurnContext(
        user_msg="what do we know about the Drake Cutlass",
        user_id=1, device_id="dev1", bot="hive",
        available_helpers=["planner", "librarian", "synthesizer"],
    )
    synth_helper = _FakeHelper(
        "synthesizer", "planner-qwen",
        {"reply": "The Drake Cutlass is a multi-role ship.", "actions": []},
    )
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {
                "summary": "look up in vault",
                "delegations": [
                    {"role": "librarian", "goal": "find cutlass notes",
                     "inputs": {"query": "Drake Cutlass"}},
                ],
            },
        ),
        "librarian": _FakeHelper(
            "librarian", "no-model",
            {"summary": "1 vault hit", "hits": [{"path": "cutlass.md",
                                                  "excerpt": "multi-role ship"}]},
        ),
        "synthesizer": synth_helper,
    }
    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    turn = await coord.coordinate(ctx, em)

    assert turn.reply, "final_reply must be non-empty"
    assert synth_helper.invoked_with, "synthesizer must have been called"
    assert turn.synth_result is not None, "synth_result must be set on turn"
    assert turn.synth_result.error is None
    # synth_mode must not be coordinator-bypass — synth ran.
    explicit = getattr(turn, "synth_mode", None)
    assert explicit != "coordinator-bypass", (
        f"synth_mode should not be coordinator-bypass; got {explicit!r}"
    )


# ---------------------------------------------------------------- skill_runner path


@pytest.mark.asyncio
async def test_skill_runner_delegation_reaches_synthesizer(catalog):
    """skill_runner result must flow through the synthesizer, not bypass it."""
    ctx = TurnContext(
        user_msg="run the daily summary skill",
        user_id=1, device_id="dev1", bot="hive",
        available_helpers=["planner", "skill_runner", "synthesizer"],
    )
    synth_helper = _FakeHelper(
        "synthesizer", "planner-qwen",
        {"reply": "I ran the daily summary skill.", "actions": []},
    )
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {
                "summary": "run skill",
                "delegations": [
                    {"role": "skill_runner", "goal": "run daily-summary",
                     "inputs": {"skill": "daily-summary"}},
                ],
            },
        ),
        "skill_runner": _FakeHelper(
            "skill_runner", "no-model",
            {"summary": "skill completed", "output": "Summary: all clear."},
        ),
        "synthesizer": synth_helper,
    }
    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    turn = await coord.coordinate(ctx, em)

    assert turn.reply, "final_reply must be non-empty"
    assert synth_helper.invoked_with, "synthesizer must have been called"
    assert turn.synth_result is not None
    assert turn.synth_result.error is None
    explicit = getattr(turn, "synth_mode", None)
    assert explicit != "coordinator-bypass"


# ---------------------------------------------------------------- direct_reply is tagged


@pytest.mark.asyncio
async def test_direct_reply_is_tagged_compose_skipped_by_design(catalog):
    """Planner direct_reply (no delegations) is a legitimate synth skip.
    It must be tagged 'compose-skipped-by-design', not 'coordinator-bypass'."""
    ctx = TurnContext(
        user_msg="hi",
        user_id=1, device_id="dev1", bot="hive",
        available_helpers=["planner", "synthesizer"],
    )
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {"summary": "small talk", "direct_reply": "Hey there!", "delegations": []},
        ),
        "synthesizer": _FakeHelper("synthesizer", "planner-qwen", {"reply": "unused"}),
    }
    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    turn = await coord.coordinate(ctx, em)

    assert turn.reply == "Hey there!"
    assert helpers["synthesizer"].invoked_with == [], "synth must NOT be called"
    assert turn.synth_mode == "compose-skipped-by-design"


# ---------------------------------------------------------------- planner failure tagged


@pytest.mark.asyncio
async def test_planner_failure_tagged_skipped_by_design(catalog):
    """Planner failure path must be tagged 'compose-skipped-by-design',
    not 'coordinator-bypass'. There's nothing to synthesize from."""
    ctx = TurnContext(
        user_msg="do something",
        user_id=1, device_id="dev1", bot="hive",
        available_helpers=["planner", "synthesizer"],
    )
    helpers = {
        "planner": _FakeHelper("planner", "qwen-7b", {}, error="model crashed"),
        "synthesizer": _FakeHelper("synthesizer", "planner-qwen", {"reply": "unused"}),
    }
    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    turn = await coord.coordinate(ctx, em)

    assert turn.error is not None
    assert turn.synth_mode == "compose-skipped-by-design"
    assert helpers["synthesizer"].invoked_with == []


# ---------------------------------------------------------------- synth_mode from turn log record


@pytest.mark.asyncio
async def test_record_turn_log_picks_up_compose_mode(tmp_path, catalog):
    """End-to-end: after a successful librarian+synth turn, record_turn_log
    must write synth_mode='compose' into the JSONL entry."""
    import json as _json
    from gateway.hive_turn_helpers import record_turn_log
    from gateway.turn_log import TurnLogStore

    store = TurnLogStore(tmp_path / "tl")

    class _AppState:
        turn_log_store = store
        helpers = {}

    ctx = TurnContext(
        user_msg="what is star citizen",
        user_id=2, device_id="dev2", bot="hive",
        available_helpers=["planner", "librarian", "synthesizer"],
    )
    _synth = HelperResult(
        role="synthesizer", model_id="planner-qwen",
        output={"reply": "A space game.", "actions": []},
    )

    class _FakeTurn:
        turn_id = "tk-test01"
        planner_result = HelperResult(
            role="planner", model_id="qwen-7b",
            output={"summary": "lib lookup", "delegations": [{"role": "librarian"}]},
        )
        helper_results = [
            HelperResult(role="librarian", model_id="no-model",
                         output={"summary": "found 1 hit"}),
        ]
        critic_result = None
        synth_mode = None  # let _compute_synth_mode derive it
        actions: list = []
        receipts: list = []
        reply = "A space game."
        blocked = False
        total_tokens = 60
        total_latency_ms = 500
        error = None

    _FakeTurn.synth_result = _synth

    await record_turn_log(
        _AppState(), _FakeTurn(),
        user_id=2, device_id="dev2", text="what is star citizen",
    )
    entries = store.tail(1)
    assert entries, "no entry written"
    assert entries[0]["synthesis"]["mode"] == "compose"
