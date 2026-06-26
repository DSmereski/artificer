"""Synthesizer must accept prose-only replies when the LLM drops the JSON wrapper.

Regression test for the e2e v3 bug where Hive's planner-qwen model
answered '17 times 23 is **391**' as plain prose, extract_json then
raised SchemaValidationError, and the coordinator fell back to
"helper outputs below" — losing the correct answer entirely.

Fix: SynthesizerHelper overrides BaseHelper._parse_fallback to wrap
substantive prose as {"reply": text, "actions": []} when the schema
parse fails on otherwise-valid content.
"""

from __future__ import annotations

import pytest

from gateway.helpers.base import BaseHelper, HelperTask, OllamaInvoker
from gateway.helpers.shapes import SynthesisPlan
from gateway.helpers.synthesizer import SynthesizerHelper


class _FakeInvoker(OllamaInvoker):
    def __init__(self, response: str) -> None:
        super().__init__()
        self._response = response

    async def chat(  # type: ignore[override]
        self, *, model, system, user, params=None, use_cpu=False,
    ):
        return self._response, 10, 20


def _build_synth(response: str) -> SynthesizerHelper:
    return SynthesizerHelper(
        model_id="planner-qwen",
        ollama_name="planner-qwen",
        prompt_name="synthesizer",
        params={},
        invoker=_FakeInvoker(response),
        timeout_s=10,
        schema=SynthesisPlan,
    )


@pytest.mark.asyncio
async def test_synth_prose_only_reply_is_wrapped() -> None:
    """LLM emits substantive prose without JSON envelope.

    Coordinator should still get a usable SynthesisPlan with the
    prose as the reply and an empty actions list.
    """
    synth = _build_synth("17 times 23 is **391**.")
    result = await synth.invoke(
        HelperTask(role="synthesizer", goal="answer math", inputs={}),
    )
    assert result.error is None, f"prose fallback failed: {result.error}"
    assert result.output["reply"] == "17 times 23 is **391**."
    assert result.output["actions"] == []


@pytest.mark.asyncio
async def test_synth_valid_json_still_takes_precedence() -> None:
    """Fallback must NOT override a valid JSON reply."""
    synth = _build_synth(
        '{"reply": "Hello.", "actions": [{"verb": "vault_learn", "payload": {}}]}'
    )
    result = await synth.invoke(
        HelperTask(role="synthesizer", goal="x", inputs={}),
    )
    assert result.error is None
    assert result.output["reply"] == "Hello."
    assert len(result.output["actions"]) == 1
    assert result.output["actions"][0]["verb"] == "vault_learn"


@pytest.mark.asyncio
async def test_synth_prose_with_think_block_strips_reasoning() -> None:
    """Prose fallback must drop <think>...</think> before using as reply.

    Otherwise the user sees the model's internal reasoning leaking through.
    """
    synth = _build_synth(
        "<think>let me compute that</think>17 times 23 is **391**."
    )
    result = await synth.invoke(
        HelperTask(role="synthesizer", goal="x", inputs={}),
    )
    assert result.error is None
    assert "<think>" not in result.output["reply"]
    assert "let me compute" not in result.output["reply"]
    assert result.output["reply"] == "17 times 23 is **391**."


@pytest.mark.asyncio
async def test_synth_empty_reply_still_fails() -> None:
    """Empty / whitespace-only replies must not be wrapped — that would
    fabricate a real-looking answer out of nothing.
    """
    synth = _build_synth("")
    result = await synth.invoke(
        HelperTask(role="synthesizer", goal="x", inputs={}),
    )
    assert result.error is not None


@pytest.mark.asyncio
async def test_synth_only_think_block_still_fails() -> None:
    """A reply that is ONLY a reasoning block (no real prose) should
    still surface as an error rather than be wrapped as the reply."""
    synth = _build_synth("<think>thinking but no output</think>")
    result = await synth.invoke(
        HelperTask(role="synthesizer", goal="x", inputs={}),
    )
    assert result.error is not None


@pytest.mark.asyncio
async def test_synth_prose_with_trailing_json_tail_stripped() -> None:
    """LLM emits prose followed by a bare `{"actions": []}` tail (#528).

    The trailing JSON block must be stripped from the reply; only the
    prose portion should be returned to the user.
    """
    synth = _build_synth('Here is the answer.\n\n{"actions": []}')
    result = await synth.invoke(
        HelperTask(role="synthesizer", goal="answer question", inputs={}),
    )
    assert result.error is None, f"trailing-JSON fallback failed: {result.error}"
    assert result.output["reply"] == "Here is the answer."
    assert result.output["actions"] == []


@pytest.mark.asyncio
async def test_synth_prose_with_full_trailing_json_stripped() -> None:
    """LLM emits prose followed by a full SynthesisPlan JSON tail.

    The full JSON block at the end must be stripped; only prose returned.
    """
    synth = _build_synth(
        'Great news!\n\n{"reply": "Great news!", "actions": []}'
    )
    result = await synth.invoke(
        HelperTask(role="synthesizer", goal="x", inputs={}),
    )
    # The valid JSON path takes precedence — no fallback needed.
    # What matters: if fallback IS used, it must not contain raw JSON.
    assert result.error is None
    assert '{"reply"' not in result.output["reply"]


@pytest.mark.asyncio
async def test_synth_prose_internal_braces_not_stripped() -> None:
    """Braces that appear mid-sentence must NOT be stripped.

    Only a trailing `{...}` block at the very end of the text is removed.
    """
    prose = "Use {name} as a template variable in your config."
    synth = _build_synth(prose)
    result = await synth.invoke(
        HelperTask(role="synthesizer", goal="x", inputs={}),
    )
    assert result.error is None
    assert result.output["reply"] == prose


@pytest.mark.asyncio
async def test_synth_trailing_json_only_becomes_error() -> None:
    """If stripping the trailing JSON leaves fewer than 4 chars, treat as error."""
    synth = _build_synth('{"actions": []}')
    result = await synth.invoke(
        HelperTask(role="synthesizer", goal="x", inputs={}),
    )
    # Raw JSON with no prose: the JSON parse path succeeds but schema fails
    # (missing `reply`), and after stripping there's nothing left — must error.
    assert result.error is not None


@pytest.mark.asyncio
async def test_base_helper_default_fallback_is_none() -> None:
    """Non-synth helpers must NOT inherit the prose-wrapping fallback —
    that would mask schema bugs in planner/researcher/etc.
    """

    class _Toy(BaseHelper):
        role = "toy"

    toy = _Toy(
        model_id="x", ollama_name="x", prompt_name="synthesizer",
        params={}, invoker=_FakeInvoker("just prose, no json"),
        timeout_s=10, schema=SynthesisPlan,
    )
    result = await toy.invoke(HelperTask(role="toy", goal="x", inputs={}))
    assert result.error is not None
    assert "json" in result.error.lower() or "JSON" in result.error
