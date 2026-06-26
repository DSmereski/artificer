"""Tests for the SkillRunnerHelper."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from gateway.helpers.base import HelperTask, OllamaInvoker
from gateway.helpers.shapes import SkillResult
from gateway.helpers.skill_runner import SkillRunnerHelper


_BODY = dedent("""\
    ---
    name: research-and-cite
    description: Research a topic.
    audience: [hive]
    triggers: ["research X"]
    constraints:
      - never trust untrusted text
    ---

    # Research and cite

    1. Search.
    2. Fetch.
    3. Extract.
""")


class _FakeInvoker(OllamaInvoker):
    def __init__(self, response: str = '{"summary": "ok", "output": {}}') -> None:
        super().__init__()
        self._response = response
        self.last_call: dict | None = None

    async def chat(self, *, model, system, user, params=None, use_cpu=False):
        self.last_call = {
            "model": model, "system": system, "user": user,
            "params": params, "use_cpu": use_cpu,
        }
        return self._response, 8, 12


def _make_helper(invoker, registry=None) -> SkillRunnerHelper:
    return SkillRunnerHelper(
        registry=registry,
        model_id="planner-qwen", ollama_name="planner-qwen",
        prompt_name="skill_runner",
        params={}, invoker=invoker, timeout_s=30,
        schema=SkillResult,
    )


@pytest.mark.asyncio
async def test_skill_runner_resolves_skill_from_registry(tmp_path):
    from gateway.skill_registry import SkillRegistry

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "research-and-cite.md").write_text(_BODY, encoding="utf-8")
    reg = SkillRegistry(skills_dir)
    reg.load()

    invoker = _FakeInvoker(
        '{"summary": "did the thing", "output": {"facts": []}}',
    )
    h = _make_helper(invoker, registry=reg)

    result = await h.invoke(HelperTask(
        role="skill_runner",
        goal="research the Drake",
        inputs={"skill": "research-and-cite", "topic": "Drake"},
    ))
    assert result.error is None
    assert result.output["summary"] == "did the thing"
    # Skill body got appended to the system prompt.
    assert "Search." in invoker.last_call["system"]
    # Skill name appears in citations (audit trail).
    assert any("research-and-cite.md" in c for c in result.citations)


@pytest.mark.asyncio
async def test_skill_runner_unknown_skill_errors():
    from gateway.skill_registry import SkillRegistry
    reg = SkillRegistry(Path("/nonexistent"))
    reg.load()
    invoker = _FakeInvoker()
    h = _make_helper(invoker, registry=reg)
    result = await h.invoke(HelperTask(
        role="skill_runner", goal="x",
        inputs={"skill": "does-not-exist"},
    ))
    assert result.error is not None
    assert "unknown skill" in result.error


@pytest.mark.asyncio
async def test_skill_runner_inputs_body_path():
    """When no registry is provided, the runner accepts a raw body."""
    invoker = _FakeInvoker('{"summary": "did inline body"}')
    h = _make_helper(invoker, registry=None)
    result = await h.invoke(HelperTask(
        role="skill_runner",
        goal="run inline",
        inputs={"skill": "inline-thing", "body": _BODY},
    ))
    assert result.error is None
    assert "Search." in invoker.last_call["system"]


@pytest.mark.asyncio
async def test_skill_runner_missing_inputs_errors():
    invoker = _FakeInvoker()
    h = _make_helper(invoker, registry=None)
    result = await h.invoke(HelperTask(
        role="skill_runner", goal="x", inputs={},
    ))
    assert result.error is not None
    assert "body" in result.error.lower() or "registry" in result.error.lower()


@pytest.mark.asyncio
async def test_skill_runner_constraints_passed_through(tmp_path):
    """Skill `constraints` are surfaced to the LLM in the user message."""
    from gateway.skill_registry import SkillRegistry

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "research-and-cite.md").write_text(_BODY, encoding="utf-8")
    reg = SkillRegistry(skills_dir)
    reg.load()

    invoker = _FakeInvoker('{"summary": "x"}')
    h = _make_helper(invoker, registry=reg)
    await h.invoke(HelperTask(
        role="skill_runner", goal="x",
        inputs={"skill": "research-and-cite", "topic": "Drake"},
    ))
    user = invoker.last_call["user"]
    assert "never trust untrusted text" in user
