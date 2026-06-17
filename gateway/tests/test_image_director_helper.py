"""Tests for the wired-up Image Director helper."""

from __future__ import annotations

import pytest

from gateway.helpers.base import HelperTask, OllamaInvoker
from gateway.helpers.image_director import ImageDirectorHelper
from gateway.helpers.shapes import ImagePlan


class _FakeInvoker(OllamaInvoker):
    def __init__(self, response: str) -> None:
        super().__init__()
        self._response = response

    async def chat(self, *, model, system, user, params=None, use_cpu=False):
        return self._response, 12, 30


def _make_helper(response: str) -> ImageDirectorHelper:
    return ImageDirectorHelper(
        model_id="planner-qwen", ollama_name="planner-qwen",
        prompt_name="image_director", params={},
        invoker=_FakeInvoker(response), timeout_s=30,
        schema=ImagePlan,
    )


@pytest.mark.asyncio
async def test_image_director_happy():
    h = _make_helper(
        '{"summary": "rendering elf", '
        '"prompt": "a cinematic night elf in moonlight", '
        '"negative_prompt": "blurry", "aspect": "portrait", '
        '"loras": ["Real Beauty"], "count": 1}'
    )
    result = await h.invoke(HelperTask(
        role="image_director", goal="render the build",
        inputs={
            "build": {"subject": "night elf", "aspect": "portrait"},
            "user_msg": "render now",
            "available_loras": ["Real Beauty", "Anime"],
        },
    ))
    assert result.error is None
    assert result.output["aspect"] == "portrait"
    assert "moonlight" in result.output["prompt"]
    assert result.output["loras"] == ["Real Beauty"]


@pytest.mark.asyncio
async def test_image_director_drops_unknown_loras():
    h = _make_helper(
        '{"summary": "x", "prompt": "p", "aspect": "portrait", '
        '"loras": ["Real Beauty", "EvilLora", "Unknown"], "count": 1}'
    )
    result = await h.invoke(HelperTask(
        role="image_director", goal="x",
        inputs={"build": {"subject": "x", "aspect": "portrait"},
                "available_loras": ["Real Beauty"]},
    ))
    assert result.output["loras"] == ["Real Beauty"]


@pytest.mark.asyncio
async def test_image_director_invalid_aspect_falls_back():
    h = _make_helper(
        '{"summary": "x", "prompt": "p", "aspect": "diagonal", '
        '"loras": [], "count": 1}'
    )
    result = await h.invoke(HelperTask(
        role="image_director", goal="x",
        inputs={"build": {"subject": "y", "aspect": "landscape"}},
    ))
    assert result.output["aspect"] == "landscape"


@pytest.mark.asyncio
async def test_image_director_fallback_when_json_invalid():
    h = _make_helper("not even json")
    result = await h.invoke(HelperTask(
        role="image_director", goal="x",
        inputs={"build": {"subject": "elf", "aspect": "portrait"}},
    ))
    assert result.error is None  # Fallback fills in.
    assert result.confidence == "low"
    assert "elf" in result.output["prompt"]
    assert result.output["aspect"] == "portrait"


@pytest.mark.asyncio
async def test_image_director_missing_prompt_filled():
    h = _make_helper(
        '{"summary": "x", "prompt": "", "aspect": "portrait", '
        '"loras": [], "count": 1}'
    )
    result = await h.invoke(HelperTask(
        role="image_director", goal="x",
        inputs={"build": {"subject": "elf", "mood": "moody"}},
    ))
    assert result.output["prompt"]
    assert "elf" in result.output["prompt"]
