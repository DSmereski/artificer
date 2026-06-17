"""Tests for the M2.2 helper base class + factory."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, Field

from gateway.helpers.base import (
    BaseHelper, HelperTask, OllamaInvoker, ResultBuilder,
    SchemaValidationError, extract_json, parse_with_schema,
)
from gateway.helpers.shapes import HelperPlan, ImagePlan, ResearchPlan


class _Toy(BaseModel):
    summary: str
    confidence: str = "medium"
    plan: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- json extraction


def test_extract_json_raw() -> None:
    obj = extract_json('{"a": 1}')
    assert obj == {"a": 1}


def test_extract_json_fenced() -> None:
    obj = extract_json('Sure!\n```json\n{"a": 2}\n```\nNotes.')
    assert obj == {"a": 2}


def test_extract_json_with_prose_preamble() -> None:
    obj = extract_json('Here you go: {"a": 3, "b": [1, 2]}')
    assert obj == {"a": 3, "b": [1, 2]}


def test_extract_json_empty_raises() -> None:
    with pytest.raises(SchemaValidationError):
        extract_json("")


def test_extract_json_no_braces_raises() -> None:
    with pytest.raises(SchemaValidationError):
        extract_json("just words, no JSON")


def test_parse_with_schema_validates() -> None:
    out = parse_with_schema('{"summary": "x", "confidence": "high"}', _Toy)
    assert out.summary == "x"
    assert out.confidence == "high"


def test_parse_with_schema_extra_fields_allowed() -> None:
    # Schemas use extra='allow' so unexpected fields don't fail.
    out = parse_with_schema('{"summary": "x", "extra": 42}', _Toy)
    assert out.summary == "x"


def test_parse_with_schema_missing_required_raises() -> None:
    with pytest.raises(SchemaValidationError):
        parse_with_schema('{"confidence": "high"}', _Toy)


# ---------------------------------------------------------------- result builder


def test_result_builder_records_tokens_and_latency() -> None:
    rb = ResultBuilder(role="planner", model_id="qwen-7b")
    rb.add_tokens(100, 50)
    rb.output = {"summary": "x"}
    rb.plan = ["a", "b"]
    rb.citations = ["http://x"]
    rb.confidence = "high"
    out = rb.build()
    assert out.tokens_in == 100
    assert out.tokens_out == 50
    assert out.latency_ms >= 0
    assert out.error is None
    assert out.confidence == "high"


def test_result_builder_fail_sets_error() -> None:
    rb = ResultBuilder(role="planner", model_id="qwen-7b")
    out = rb.fail("boom").build()
    assert out.error == "boom"


# ---------------------------------------------------------------- base helper


class _FakeInvoker(OllamaInvoker):
    """Minimal invoker that returns a canned response."""

    def __init__(self, response: str, t_in: int = 10, t_out: int = 20) -> None:
        super().__init__()
        self._response = response
        self._t_in = t_in
        self._t_out = t_out
        self.last_call: dict[str, Any] | None = None

    async def chat(  # type: ignore[override]
        self, *, model, system, user, params=None, use_cpu=False,
    ):
        self.last_call = {
            "model": model, "system": system, "user": user,
            "params": params, "use_cpu": use_cpu,
        }
        return self._response, self._t_in, self._t_out


@pytest.fixture
def planner_helper(tmp_path, monkeypatch):
    """Construct a Planner helper that uses the real planner.md prompt."""
    from gateway.helpers.planner import PlannerHelper
    invoker = _FakeInvoker(response='{"summary": "do X", "delegations": [], "plan": ["X"]}')
    h = PlannerHelper(
        model_id="qwen-7b", ollama_name="qwen2.5:7b",
        prompt_name="planner",
        params={"temperature": 0.3},
        invoker=invoker,
        timeout_s=30,
        schema=HelperPlan,
    )
    return h, invoker


@pytest.mark.asyncio
async def test_planner_invoke_happy(planner_helper) -> None:
    helper, invoker = planner_helper
    task = HelperTask(
        role="planner",
        goal="decide what to do",
        inputs={"user_msg": "hi"},
        constraints=["read-only"],
    )
    result = await helper.invoke(task)
    assert result.error is None
    assert result.role == "planner"
    assert result.model_id == "qwen-7b"
    assert result.tokens_in == 10
    assert result.tokens_out == 20
    assert result.output["summary"] == "do X"
    assert result.plan == ["X"]
    # The user message should include goal + inputs (quarantine check).
    assert "decide what to do" in invoker.last_call["user"]
    assert "user_msg" in invoker.last_call["user"]


@pytest.mark.asyncio
async def test_planner_invoke_invalid_json_returns_error(monkeypatch) -> None:
    from gateway.helpers.planner import PlannerHelper
    invoker = _FakeInvoker(response="not json at all")
    h = PlannerHelper(
        model_id="qwen-7b", ollama_name="qwen2.5:7b",
        prompt_name="planner",
        params={}, invoker=invoker, timeout_s=10,
        schema=HelperPlan,
    )
    task = HelperTask(role="planner", goal="x", inputs={})
    result = await h.invoke(task)
    assert result.error is not None
    assert "JSON" in result.error or "json" in result.error.lower()


@pytest.mark.asyncio
async def test_helper_use_cpu_passes_through_to_invoker() -> None:
    # Use a helper that goes through BaseHelper.invoke (the planner does;
    # the researcher overrides invoke for the pipeline).
    from gateway.helpers.planner import PlannerHelper
    from gateway.helpers.shapes import HelperPlan
    invoker = _FakeInvoker(response='{"summary": "s", "delegations": []}')
    h = PlannerHelper(
        model_id="qwen-8b", ollama_name="qwen3:8b",
        prompt_name="planner",
        params={}, invoker=invoker, timeout_s=10,
        schema=HelperPlan,
    )
    task = HelperTask(role="planner", goal="x", inputs={}, use_cpu=True)
    result = await h.invoke(task)
    assert result.error is None
    assert invoker.last_call["use_cpu"] is True


# ---------------------------------------------------------------- factory


def test_factory_builds_all_helpers(tmp_path) -> None:
    from gateway.helpers.factory import build_helpers
    from gateway.model_catalog import load_catalog
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    catalog = load_catalog(project_root / "config" / "model_catalog.yaml")
    pool = build_helpers(catalog, invoker=_FakeInvoker(response='{"summary":"x"}'))
    expected = {"planner", "coder", "researcher", "image_director",
                "sysmon", "summarizer", "critic", "librarian",
                "synthesizer", "skill_runner"}
    assert expected.issubset(set(pool.keys()))
    # All helpers now share planner-qwen (one voice across the hive).
    assert pool["planner"].model_id == "planner-qwen"
    assert pool["coder"].model_id == "planner-qwen"
    assert pool["image_director"].model_id == "planner-qwen"
    assert pool["synthesizer"].model_id == "planner-qwen"


@pytest.mark.asyncio
async def test_helper_missing_prompt_returns_error() -> None:
    from gateway.helpers.planner import PlannerHelper
    invoker = _FakeInvoker(response="{}")
    h = PlannerHelper(
        model_id="qwen-7b", ollama_name="qwen2.5:7b",
        prompt_name="does-not-exist",
        params={}, invoker=invoker, timeout_s=10,
        schema=HelperPlan,
    )
    result = await h.invoke(HelperTask(role="planner", goal="x", inputs={}))
    assert result.error is not None
    assert "does-not-exist" in result.error or "not found" in result.error


# ---------------------------------------------------------------- repair


def test_extract_json_repairs_trailing_comma() -> None:
    obj = extract_json('{"a": 1, "b": 2,}')
    assert obj == {"a": 1, "b": 2}


def test_extract_json_repairs_trailing_comma_in_array() -> None:
    obj = extract_json('{"items": [1, 2, 3,]}')
    assert obj == {"items": [1, 2, 3]}


def test_extract_json_repairs_unescaped_newline_in_string() -> None:
    obj = extract_json('{"reply": "first line\nsecond line"}')
    assert obj == {"reply": "first line\nsecond line"}


def test_extract_json_leading_whitespace() -> None:
    obj = extract_json('\n\n{"a": 1}')
    assert obj == {"a": 1}


def test_extract_json_strips_unclosed_think_block() -> None:
    """qwen3 sometimes runs out of budget mid-think and emits the
    JSON without ever closing the <think> tag. Old regex only matched
    closed blocks; new one falls back to stripping <think>... up to
    the first { or [."""
    text = '<think>\nThinking Process:\n  steps...\n  more thinking\n{"answer": 42}'
    obj = extract_json(text)
    assert obj == {"answer": 42}


def test_extract_json_strips_unclosed_think_with_array() -> None:
    text = '<think>reasoning\n[1, 2, 3]'
    obj = extract_json(text)
    assert obj == [1, 2, 3]


def test_extract_json_handles_closed_then_open_think() -> None:
    """Pathological case: one closed, then a stray opener."""
    text = '<think>first</think>middle<think>second\n{"x": 1}'
    obj = extract_json(text)
    assert obj == {"x": 1}


# ---------------------------------------------------------------- plan §1.5

def test_prompt_version_is_stable_short_hash():
    from gateway.helpers.base import prompt_version

    v1 = prompt_version("planner")
    v2 = prompt_version("planner")
    assert v1 == v2                # stable across calls
    assert len(v1) == 12           # short tag
    assert v1 != "missing"         # planner.md exists


def test_prompt_version_missing_file():
    from gateway.helpers.base import prompt_version

    assert prompt_version("does_not_exist_prompt_xyz") == "missing"


def test_distinct_prompts_have_distinct_versions():
    from gateway.helpers.base import prompt_version

    assert prompt_version("planner") != prompt_version("synthesizer")
