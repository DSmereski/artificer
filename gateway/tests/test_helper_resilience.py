"""P3 — Helper resilience tests.

Verifies three behaviour contracts added in the P3 phase of the
Hive Reasoning Upgrade:

1. Recoverable malformed output (prose containing the required fields,
   but not wrapped in valid JSON) -> fallback recovery succeeds, result
   is used, prose_rescue=True, no error.

2. Nonsense output (schema-structurally valid JSON but semantically
   meaningless -- e.g. empty/placeholder required strings) -> REJECTED
   by semantic validator -> safe default used, HelperResult.error set,
   rejection logged at WARNING level.

3. Clean valid output -> used unchanged, no error, no prose_rescue flag.

Also tests that _semantically_valid itself correctly accepts/rejects
various edge cases independently of the helper invocation path.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from pydantic import BaseModel, Field

from gateway.helpers.base import (
    BaseHelper,
    HelperTask,
    OllamaInvoker,
    SchemaValidationError,
    _semantically_valid,
)


# ---------------------------------------------------------------- test schema


class _StageSchema(BaseModel):
    """Minimal schema that mimics the shape that exposed the live bug:
    a required string `stage_id` (the field that came back empty) and
    a required string `summary`.  Mirrors the constellation synthesizer
    failure mode.
    """

    stage_id: str
    summary: str
    steps: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- fake invoker


class _FakeInvoker(OllamaInvoker):
    """Returns a canned text response without touching Ollama."""

    def __init__(self, response: str) -> None:
        super().__init__()
        self._response = response

    async def chat(  # type: ignore[override]
        self,
        *,
        model: str,
        system: str,
        user: str,
        params: dict | None = None,
        use_cpu: bool = False,
        fmt: dict | None = None,
        tools: list | None = None,
    ) -> tuple[str, int, int]:
        return self._response, 5, 10


# ---------------------------------------------------------------- helper factories


def _make_helper(
    response: str,
    *,
    safe_default: dict | None = None,
) -> BaseHelper:
    """Build a BaseHelper (not a subclass) wired to the fake invoker."""
    return BaseHelper(
        model_id="test-model",
        ollama_name="test-ollama",
        prompt_name="planner",       # planner.md exists; content irrelevant
        params={},
        invoker=_FakeInvoker(response),
        timeout_s=10,
        schema=_StageSchema,
        safe_default=safe_default,
    )


def _task() -> HelperTask:
    return HelperTask(role="test", goal="run test", inputs={})


# ---------------------------------------------------------------- prose-recovery subclass


class _RecoveringHelper(BaseHelper):
    """Simulates a role-specific _parse_fallback (like the synthesizer).

    Extracts stage_id and summary from key: value prose when JSON parsing
    fails entirely.
    """

    role = "test-recovering"

    def _parse_fallback(
        self, text: str, error: SchemaValidationError,
    ) -> dict[str, Any] | None:
        if "stage_id:" in text and "summary:" in text:
            return {
                "stage_id": "prose-recovered",
                "summary": text[:80].strip(),
            }
        return None


# ================================================================ scenario 1
# Recoverable malformed output -> subclass fallback recovers it.
# ================================================================


@pytest.mark.asyncio
async def test_recoverable_prose_subclass_fallback() -> None:
    """Subclass _parse_fallback recovers prose -> prose_rescue=True, no error."""
    prose_response = "stage_id: alpha  summary: Build done  steps: run, test"
    h = _RecoveringHelper(
        model_id="test-model",
        ollama_name="test",
        prompt_name="planner",
        params={},
        invoker=_FakeInvoker(prose_response),
        timeout_s=10,
        schema=_StageSchema,
    )
    result = await h.invoke(_task())

    assert result.error is None, f"unexpected error: {result.error}"
    assert result.prose_rescue is True
    assert result.output["stage_id"] == "prose-recovered"
    assert result.output["summary"]   # non-empty string


@pytest.mark.asyncio
async def test_recovered_output_with_semantic_nonsense_is_rejected() -> None:
    """Subclass fallback returns dict with empty stage_id -> semantic validator rejects."""

    class _BadRecoveryHelper(BaseHelper):
        role = "test-bad-recovery"

        def _parse_fallback(
            self, text: str, error: SchemaValidationError,
        ) -> dict[str, Any] | None:
            # Returns a dict that passes Pydantic but is semantically empty.
            return {"stage_id": "", "summary": "something"}

    h = _BadRecoveryHelper(
        model_id="test-model",
        ollama_name="test",
        prompt_name="planner",
        params={},
        invoker=_FakeInvoker("no json here at all"),
        timeout_s=10,
        schema=_StageSchema,
        safe_default={"stage_id": "SAFE_DEFAULT", "summary": "safe"},
    )
    result = await h.invoke(_task())

    assert result.error is not None
    assert "semantic" in result.error.lower()
    assert result.output["stage_id"] == "SAFE_DEFAULT"
    assert result.prose_rescue is False   # never set; rejection happened


@pytest.mark.asyncio
async def test_base_fallback_returns_none_when_no_json() -> None:
    """Base _parse_fallback returns None when no JSON is in the text -> error."""
    h = _make_helper("stage_id: alpha  summary: Build done")
    result = await h.invoke(_task())
    # No JSON -> parse_with_schema fails -> base fallback (extract_json) also
    # fails -> fallback returns None -> error set, prose_rescue stays False.
    assert result.error is not None
    assert result.prose_rescue is False


# ================================================================ scenario 2
# Nonsense output: schema-valid JSON but semantically rejected.
# ================================================================


@pytest.mark.asyncio
async def test_nonsense_empty_stage_id_rejected_safe_default_used(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Schema-valid JSON with empty stage_id -> rejected -> safe default + error."""
    empty_stage_id = '{"stage_id": "", "summary": "Build the system"}'
    safe = {"stage_id": "SAFE", "summary": "fallback", "steps": []}
    h = _make_helper(empty_stage_id, safe_default=safe)

    with caplog.at_level(logging.WARNING, logger="gateway.helpers"):
        result = await h.invoke(_task())

    assert result.error is not None, "error must be set on semantic rejection"
    assert "semantic" in result.error.lower()
    assert result.output == safe
    # Rejection must be logged -- check the formatted log message or extra dict.
    warning_texts = " ".join(r.getMessage() for r in caplog.records)
    assert "semantic" in warning_texts.lower(), (
        f"expected 'semantic' in log output; got: {warning_texts!r}"
    )


@pytest.mark.asyncio
async def test_placeholder_stage_id_rejected(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Known placeholder value ('null') in required field -> rejected."""
    placeholder_response = '{"stage_id": "null", "summary": "ok"}'
    h = _make_helper(placeholder_response, safe_default={"stage_id": "DEFAULT", "summary": "x"})

    with caplog.at_level(logging.WARNING, logger="gateway.helpers"):
        result = await h.invoke(_task())

    assert result.error is not None
    assert "placeholder" in result.error.lower() or "semantic" in result.error.lower()


@pytest.mark.asyncio
async def test_all_whitespace_required_string_rejected() -> None:
    """Required string that is all-whitespace -> semantic rejection."""
    whitespace_response = '{"stage_id": "   ", "summary": "ok"}'
    h = _make_helper(whitespace_response)
    result = await h.invoke(_task())
    assert result.error is not None
    lower = result.error.lower()
    assert "blank" in lower or "placeholder" in lower or "semantic" in lower


# ================================================================ scenario 3
# Clean valid output: used unchanged, no error, no prose_rescue flag.
# ================================================================


@pytest.mark.asyncio
async def test_clean_valid_output_used_unchanged() -> None:
    """A well-formed LLM reply is passed through with no error or rescue flag."""
    clean = (
        '{"stage_id": "stage-001", "summary": "Initialise build", '
        '"steps": ["step1", "step2"]}'
    )
    h = _make_helper(clean)
    result = await h.invoke(_task())

    assert result.error is None
    assert result.prose_rescue is False
    assert result.output["stage_id"] == "stage-001"
    assert result.output["summary"] == "Initialise build"
    assert result.output["steps"] == ["step1", "step2"]


# ================================================================ _semantically_valid unit tests
# ================================================================


def test_semantically_valid_accepts_good_output() -> None:
    ok, reason = _semantically_valid(
        {"stage_id": "s1", "summary": "Build done", "steps": ["a", "b"]},
        _StageSchema,
    )
    assert ok is True
    assert reason == ""


def test_semantically_valid_rejects_empty_required_string() -> None:
    ok, reason = _semantically_valid(
        {"stage_id": "", "summary": "ok"},
        _StageSchema,
    )
    assert ok is False
    assert "stage_id" in reason


def test_semantically_valid_rejects_placeholder_string() -> None:
    for placeholder in ("null", "TODO", "placeholder", "tbd", "N/A", "<id>"):
        ok, reason = _semantically_valid(
            {"stage_id": placeholder, "summary": "something real"},
            _StageSchema,
        )
        assert ok is False, f"should have rejected placeholder {placeholder!r}"
        assert "stage_id" in reason


def test_semantically_valid_rejects_whitespace_required_string() -> None:
    ok, reason = _semantically_valid(
        {"stage_id": "   \t  ", "summary": "ok"},
        _StageSchema,
    )
    assert ok is False
    assert "stage_id" in reason


def test_semantically_valid_rejects_list_of_empty_strings_in_required_field() -> None:
    """A required list that only contains empty strings is nonsense."""

    class _ListRequired(BaseModel):
        items: list[str]   # no default -> required
        label: str

    ok, reason = _semantically_valid(
        {"items": ["", "", ""], "label": "ok"},
        _ListRequired,
    )
    assert ok is False
    assert "items" in reason


def test_semantically_valid_accepts_empty_optional_list() -> None:
    """An optional list (has default_factory) being empty is fine."""
    ok, _ = _semantically_valid(
        {"stage_id": "s1", "summary": "ok", "steps": []},
        _StageSchema,
    )
    assert ok is True


def test_semantically_valid_case_insensitive_placeholder() -> None:
    """Placeholder check is case-insensitive."""
    ok, reason = _semantically_valid(
        {"stage_id": "NULL", "summary": "ok"},
        _StageSchema,
    )
    assert ok is False
    assert "stage_id" in reason


def test_semantically_valid_accepts_short_but_real_string() -> None:
    """A genuinely short string ('a') is not a placeholder."""
    ok, _ = _semantically_valid(
        {"stage_id": "a", "summary": "b"},
        _StageSchema,
    )
    assert ok is True


def test_semantically_valid_rejects_non_dict_input() -> None:
    ok, reason = _semantically_valid("not a dict", _StageSchema)  # type: ignore[arg-type]
    assert ok is False
    assert "dict" in reason
