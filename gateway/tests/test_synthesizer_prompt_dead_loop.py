"""Synthesizer prompt must avoid the 'want me to research?' dead loop
when researcher already ran and came back empty.

Symptom we're guarding against: user asks a question -> planner
delegates to librarian + researcher -> both come back empty ->
synthesizer says "I don't have notes -- want me to research it?"
The user just got web research. Asking again loops the dead end.
"""
from __future__ import annotations

from pathlib import Path

PROMPT = Path(__file__).resolve().parents[2] / "prompts" / "synthesizer.md"


def _read() -> str:
    return PROMPT.read_text(encoding="utf-8")


def test_prompt_distinguishes_both_helpers_empty_case() -> None:
    txt = _read()
    assert "BOTH librarian AND researcher ran" in txt, (
        "synthesizer.md must explicitly handle the 'both empty' case "
        "or the LLM will reflexively suggest 'want me to research' "
        "even after research already ran"
    )


def test_prompt_warns_against_research_loop() -> None:
    txt = _read()
    # The prompt must call out the loop explicitly so the LLM doesn't
    # treat "want me to research?" as the default empty-result reply.
    lowered = txt.lower()
    assert "research already ran" in lowered, (
        "synthesizer.md must call out that researcher having run is "
        "a reason NOT to offer more research"
    )


def test_prompt_lists_both_helper_roles_as_signals() -> None:
    """The 'which case applies' decision must reference helper_results
    role scanning so the LLM knows to inspect, not guess."""
    txt = _read()
    assert "helper_results[*].role" in txt, (
        "the rule must instruct the LLM to scan role names in "
        "helper_results so it knows whether researcher already ran"
    )
