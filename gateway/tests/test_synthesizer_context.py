"""Test that _synthesize passes ctx.history_digest as 'context' to the synth task.

Regression test for the bug where the synthesizer had no view of the
MemoryStore's core_slots / mid_summary / mid_user_facts, so when helpers
came back unhelpful it had nothing to fall back on for recall questions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.event_emitter import ListEmitter
from gateway.helpers.base import HelperResult, HelperTask
from gateway.hive_coordinator import HiveCoordinator, TurnContext
from gateway.model_catalog import load_catalog


# ---------------------------------------------------------------- fakes


class _FakeHelper:
    def __init__(self, role: str, model_id: str, output: dict, *,
                 plan: list[str] | None = None,
                 confidence: str = "high",
                 error: str | None = None) -> None:
        self.role = role
        self.model_id = model_id
        self._output = output
        self._plan = plan or []
        self._confidence = confidence
        self._error = error
        self.invoked_with: list[HelperTask] = []

    async def invoke(self, task: HelperTask) -> HelperResult:
        self.invoked_with.append(task)
        return HelperResult(
            role=self.role, model_id=self.model_id,
            output=self._output, plan=self._plan,
            confidence=self._confidence,
            error=self._error,
            tokens_in=10, tokens_out=20,
            latency_ms=5,
            parent_id=task.parent_id,
        )


@pytest.fixture
def catalog():
    return load_catalog(
        Path(__file__).resolve().parents[2] / "config" / "model_catalog.yaml",
    )


# ---------------------------------------------------------------- synthesizer context test


@pytest.mark.asyncio
async def test_synthesizer_receives_history_digest_as_context(catalog):
    """Synthesizer task inputs must include 'context' = ctx.history_digest.

    Scenario: user asked us to remember the codeword 'penguin-glacier'
    in a prior turn. MemoryStore persisted it in core_slots. The planner
    routed the recall question to librarian, which came back empty/off-topic.
    The synthesizer must still be able to answer because history_digest
    carries the codeword.
    """
    codeword_digest = "user's codeword is 'penguin-glacier'"

    ctx = TurnContext(
        user_msg="what's my codeword?",
        user_id=42, device_id="dev1",
        history_digest=codeword_digest,
        image_build=None,
        skills_digest="",
        available_helpers=["planner", "librarian", "synthesizer"],
    )

    synth_helper = _FakeHelper(
        "synthesizer", "planner-qwen",
        {"reply": "Your codeword is penguin-glacier.", "actions": []},
    )

    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {
                "summary": "recall codeword from memory",
                "delegations": [
                    {
                        "role": "librarian",
                        "goal": "look up codeword",
                        "inputs": {"query": "codeword"},
                        "risky": False,
                    },
                ],
            },
        ),
        "librarian": _FakeHelper(
            "librarian", "qwen-7b",
            # Returns off-topic hits — does not contain the codeword
            {"hits": [{"path": "loras/foo.md", "excerpt": "unrelated content"}]},
        ),
        "synthesizer": synth_helper,
    }

    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    await coord.coordinate(ctx, em)

    # Synthesizer must have been invoked exactly once
    assert len(synth_helper.invoked_with) == 1, (
        "synthesizer was not invoked — check planner delegations and dispatch"
    )

    synth_task = synth_helper.invoked_with[0]

    # THE KEY ASSERTION: context must be present and contain the codeword
    assert "context" in synth_task.inputs, (
        f"synthesizer task.inputs is missing 'context' key. "
        f"Got keys: {list(synth_task.inputs.keys())}"
    )
    assert codeword_digest in synth_task.inputs["context"], (
        f"synthesizer task.inputs['context'] does not contain the history_digest. "
        f"Expected to find: {codeword_digest!r}. "
        f"Got: {synth_task.inputs['context']!r}"
    )
