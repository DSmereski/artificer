"""Tests for the TypedHelper base (Phase D.1 proof-of-concept).

`TypedHelper.parse_inputs` validates `task.inputs` against a declared
Pydantic model. Subclasses get back a typed instance, a one-line
error string on validation failure, or `None` when no model was
declared (legacy behavior).

The proof-of-concept consumer is `ChatRecallHelper` (covered by its
own tests). These tests exercise the base in isolation.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from gateway.helpers.base import HelperTask
from gateway.helpers.typed_tool import TypedHelper


class _SampleInputs(BaseModel):
    query: str
    limit: int = 5


class _SampleHelper(TypedHelper):
    role = "sample"
    Inputs: ClassVar[type[BaseModel] | None] = _SampleInputs

    def __init__(self) -> None:  # bypass BaseHelper LLM plumbing
        self.model_id = "sample-test"
        self.role = "sample"


def _task(inputs: dict) -> HelperTask:
    return HelperTask(role="sample", goal="g", inputs=inputs)


def test_parse_inputs_returns_validated_model():
    h = _SampleHelper()
    parsed = h.parse_inputs(_task({"query": "hello", "limit": 3}))
    assert isinstance(parsed, _SampleInputs)
    assert parsed.query == "hello"
    assert parsed.limit == 3


def test_parse_inputs_uses_default_when_field_missing():
    h = _SampleHelper()
    parsed = h.parse_inputs(_task({"query": "hello"}))
    assert isinstance(parsed, _SampleInputs)
    assert parsed.limit == 5


def test_parse_inputs_returns_error_string_on_invalid():
    h = _SampleHelper()
    parsed = h.parse_inputs(_task({"query": 123, "limit": "not-int"}))
    assert isinstance(parsed, str)
    assert "input validation failed" in parsed
    # Both bad fields surface in the error string.
    assert "query" in parsed
    assert "limit" in parsed


def test_parse_inputs_returns_error_when_required_field_missing():
    h = _SampleHelper()
    parsed = h.parse_inputs(_task({}))
    assert isinstance(parsed, str)
    assert "query" in parsed


def test_parse_inputs_returns_none_when_no_inputs_declared():
    """Helpers without a declared `Inputs` model fall back to legacy
    dict-access semantics — `parse_inputs` returns None and the
    caller keeps using `task.inputs.get(...)`."""
    class _Plain(TypedHelper):
        role = "plain"
        Inputs: ClassVar[type[BaseModel] | None] = None

        def __init__(self) -> None:
            self.role = "plain"
            self.model_id = "plain"

    assert _Plain().parse_inputs(_task({"x": 1})) is None


def test_chat_recall_helper_inputs_extra_allowed():
    """ChatRecallInputs uses `extra='allow'` so the planner can pass
    extra fields without breaking the helper. Regression test."""
    from gateway.helpers.chat_recall import ChatRecallInputs
    inp = ChatRecallInputs.model_validate(
        {"query": "x", "user_id": 7, "unexpected": "ok"},
    )
    assert inp.query == "x"
    assert inp.user_id == 7
