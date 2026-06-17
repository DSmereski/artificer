"""Phase 3: synthesizer prompt must teach the three new memory/entity verbs.

Without these mentions in the LLM-facing prompt, the synthesizer cannot
emit `core_memory_replace`, `core_memory_append`, or `entity_page_update`
even though the executor + risky_verbs are wired for them.
"""
from __future__ import annotations

from pathlib import Path

PROMPT = (
    Path(__file__).resolve().parents[2] / "prompts" / "synthesizer.md"
)


def _read() -> str:
    return PROMPT.read_text(encoding="utf-8")


def test_synthesizer_prompt_documents_core_memory_replace() -> None:
    txt = _read()
    assert "core_memory_replace" in txt, (
        "synthesizer.md must teach the core_memory_replace verb"
    )


def test_synthesizer_prompt_documents_core_memory_append() -> None:
    txt = _read()
    assert "core_memory_append" in txt, (
        "synthesizer.md must teach the core_memory_append verb"
    )


def test_synthesizer_prompt_documents_entity_page_update() -> None:
    txt = _read()
    assert "entity_page_update" in txt, (
        "synthesizer.md must teach the entity_page_update verb"
    )


def test_synthesizer_prompt_lists_default_core_slot_names() -> None:
    """The defaults `user_profile`, `preferences`, `open_tasks`,
    `recent_decisions`, `active_projects` should appear in the prompt
    so the LLM knows the canonical slot names rather than inventing
    new ones each turn."""
    txt = _read()
    for slot in (
        "user_profile", "preferences", "open_tasks",
        "recent_decisions", "active_projects",
    ):
        assert slot in txt, f"synthesizer.md missing default slot {slot!r}"
