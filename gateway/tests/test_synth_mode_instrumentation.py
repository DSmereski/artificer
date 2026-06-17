"""Tests for synth_mode instrumentation (Fix 1, task #448).

Verifies that _compute_synth_mode returns the correct mode string for
each of the four code paths, and that TurnLogEntry.to_jsonable() surfaces
the mode in synthesis.mode.
"""

from __future__ import annotations

import pytest

from gateway.helpers.base import HelperResult
from gateway.hive_turn_helpers import _compute_synth_mode
from gateway.turn_log import TurnLogEntry


# ---------------------------------------------------------------- _compute_synth_mode


def test_synth_mode_none_is_coordinator_bypass():
    assert _compute_synth_mode(None) == "coordinator-bypass"


def test_synth_mode_error_is_fallback():
    synth = HelperResult(role="synthesizer", model_id="m", error="timeout")
    assert _compute_synth_mode(synth) == "fallback"


def test_synth_mode_prose_rescue_flag():
    synth = HelperResult(
        role="synthesizer", model_id="m",
        output={"reply": "plain prose", "actions": []},
        prose_rescue=True,
    )
    assert _compute_synth_mode(synth) == "prose-rescue"


def test_synth_mode_compose_on_clean_json_reply():
    synth = HelperResult(
        role="synthesizer", model_id="m",
        output={"reply": "The answer is 42.", "actions": []},
    )
    assert _compute_synth_mode(synth) == "compose"


def test_synth_mode_fallback_when_reply_empty_and_no_error():
    synth = HelperResult(
        role="synthesizer", model_id="m",
        output={"reply": "", "actions": []},
    )
    assert _compute_synth_mode(synth) == "fallback"


def test_synth_mode_explicit_overrides_derived():
    synth = HelperResult(role="synthesizer", model_id="m", error="oops")
    assert _compute_synth_mode(synth, explicit="compose-skipped-by-design") == "compose-skipped-by-design"


def test_synth_mode_explicit_none_falls_through_to_derivation():
    synth = HelperResult(
        role="synthesizer", model_id="m",
        output={"reply": "hi", "actions": []},
    )
    assert _compute_synth_mode(synth, explicit=None) == "compose"


# ---------------------------------------------------------------- TurnLogEntry serialisation


def test_turn_log_entry_default_synth_mode():
    entry = TurnLogEntry(turn_id="t1", device_id="d", user_id=0)
    j = entry.to_jsonable()
    assert j["synthesis"]["mode"] == "coordinator-bypass"


def test_turn_log_entry_synth_mode_compose():
    entry = TurnLogEntry(
        turn_id="t2", device_id="d", user_id=0,
        synth_mode="compose",
        synth_reply="Hello.",
    )
    j = entry.to_jsonable()
    assert j["synthesis"]["mode"] == "compose"
    assert j["synthesis"]["reply"] == "Hello."


def test_turn_log_entry_synth_mode_prose_rescue():
    entry = TurnLogEntry(
        turn_id="t3", device_id="d", user_id=0,
        synth_mode="prose-rescue",
        synth_reply="Some prose answer.",
    )
    j = entry.to_jsonable()
    assert j["synthesis"]["mode"] == "prose-rescue"


def test_turn_log_entry_synth_mode_fallback():
    entry = TurnLogEntry(
        turn_id="t4", device_id="d", user_id=0,
        synth_mode="fallback",
        synth_error="synthesizer timed out",
    )
    j = entry.to_jsonable()
    assert j["synthesis"]["mode"] == "fallback"
    assert j["synthesis"]["error"] == "synthesizer timed out"


def test_turn_log_entry_synth_mode_skipped_by_design():
    entry = TurnLogEntry(
        turn_id="t5", device_id="d", user_id=0,
        synth_mode="compose-skipped-by-design",
        final_reply="Hey there!",
    )
    j = entry.to_jsonable()
    assert j["synthesis"]["mode"] == "compose-skipped-by-design"


# ---------------------------------------------------------------- prose_rescue flag on HelperResult


@pytest.mark.asyncio
async def test_prose_rescue_flag_set_on_synth_fallback_path():
    """When SynthesizerHelper falls back to _parse_fallback, the
    HelperResult must have prose_rescue=True so _compute_synth_mode
    returns 'prose-rescue'."""
    from gateway.helpers.base import HelperTask, OllamaInvoker
    from gateway.helpers.synthesizer import SynthesizerHelper
    from gateway.helpers.shapes import SynthesisPlan

    class _FakeInvoker(OllamaInvoker):
        async def chat(self, *, model, system, user, params=None, use_cpu=False):
            return "seventeen times twenty-three is three hundred and ninety-one", 10, 20

    synth = SynthesizerHelper(
        model_id="planner-qwen", ollama_name="planner-qwen",
        prompt_name="synthesizer", params={},
        invoker=_FakeInvoker(), timeout_s=10, schema=SynthesisPlan,
    )
    result = await synth.invoke(
        HelperTask(role="synthesizer", goal="answer", inputs={}),
    )
    assert result.error is None
    assert result.prose_rescue is True
    assert _compute_synth_mode(result) == "prose-rescue"


@pytest.mark.asyncio
async def test_prose_rescue_flag_not_set_on_clean_json():
    """A clean JSON reply must leave prose_rescue=False."""
    from gateway.helpers.base import HelperTask, OllamaInvoker
    from gateway.helpers.synthesizer import SynthesizerHelper
    from gateway.helpers.shapes import SynthesisPlan

    class _FakeInvoker(OllamaInvoker):
        async def chat(self, *, model, system, user, params=None, use_cpu=False):
            return '{"reply": "42", "actions": []}', 10, 20

    synth = SynthesizerHelper(
        model_id="planner-qwen", ollama_name="planner-qwen",
        prompt_name="synthesizer", params={},
        invoker=_FakeInvoker(), timeout_s=10, schema=SynthesisPlan,
    )
    result = await synth.invoke(
        HelperTask(role="synthesizer", goal="answer", inputs={}),
    )
    assert result.error is None
    assert result.prose_rescue is False
    assert _compute_synth_mode(result) == "compose"
