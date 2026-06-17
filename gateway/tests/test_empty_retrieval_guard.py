"""Empty-retrieval guard: when every retrieval helper came back empty
and the synthesizer's reply lacks an empty-acknowledgement phrase,
force the canonical "I couldn't find that" message.

Catches the production-observed failure where the LLM ignores Rule 8
of synthesizer.md and fabricates a confident narrative reply ("Cafe de
Shimokitazawa (Shimokitazawa 1-chome) -- spacious airy interior, quiet
mid-morning") from training data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gateway.hallucination_guard import (
    all_retrieval_helpers_empty,
    enforce_empty_retrieval_reply,
)


@dataclass
class _Hr:
    role: str
    output: dict[str, Any] | None = None
    error: str | None = None


def test_all_empty_with_no_ack_force_canonical() -> None:
    hrs = [
        _Hr(role="librarian", output={"hits": [], "summary": ""}),
        _Hr(role="researcher", output={"facts": [], "summary": ""}),
    ]
    fabricated = (
        "Cafe de Shimokitazawa is a staple local spot with a spacious, "
        "airy interior and large windows. It's known for being "
        "consistently quiet before 11 AM."
    )
    out = enforce_empty_retrieval_reply(fabricated, hrs)
    assert "couldn't find that" in out.lower(), (
        "both retrieval helpers empty + no acknowledgement -> must "
        "force the canonical 'couldn't find that' reply; got: " + out
    )


def test_all_empty_with_acknowledgement_passes_through() -> None:
    hrs = [
        _Hr(role="librarian", output={"hits": []}),
        _Hr(role="researcher", output={"facts": []}),
    ]
    reply = (
        "I couldn't find that in your vault or on the web. Try a more "
        "specific date or neighbourhood."
    )
    out = enforce_empty_retrieval_reply(reply, hrs)
    assert out == reply, "an existing acknowledgement must NOT be overwritten"


def test_one_helper_has_signal_passes_through() -> None:
    hrs = [
        _Hr(role="librarian",
            output={"hits": [{"path": "trips/japan.md",
                              "excerpt": "Haneda is 14 hours"}]}),
        _Hr(role="researcher", output={"facts": []}),
    ]
    reply = "Haneda is about 14 hours from Toronto, give or take."
    out = enforce_empty_retrieval_reply(reply, hrs)
    assert out == reply, (
        "librarian had real hits -> guard must NOT fire even when "
        "researcher was empty"
    )


def test_helpers_errored_treated_as_empty() -> None:
    hrs = [
        _Hr(role="librarian", error="vault unreachable"),
        _Hr(role="researcher", error="DDG ratelimit"),
    ]
    fab = "The answer is 42."
    out = enforce_empty_retrieval_reply(fab, hrs)
    assert "couldn't find" in out.lower()


def test_no_retrieval_helpers_at_all_passes_through() -> None:
    # A turn with only planner / sysmon / chat_recall — no retrieval
    # helpers ran, so the empty-retrieval decision doesn't apply.
    hrs = [
        _Hr(role="planner", output={"summary": "direct"}),
        _Hr(role="chat_recall", output={"summary": "no prior"}),
    ]
    out = enforce_empty_retrieval_reply("Hello", hrs)
    assert out == "Hello"


def test_all_retrieval_helpers_empty_helper_basics() -> None:
    """Sanity tests on the predicate underlying the guard."""
    assert all_retrieval_helpers_empty([
        _Hr(role="librarian", output={"hits": []}),
        _Hr(role="researcher", output={"facts": []}),
    ]) is True
    assert all_retrieval_helpers_empty([
        _Hr(role="librarian", output={"hits": [{"path": "x.md"}]}),
        _Hr(role="researcher", output={"facts": []}),
    ]) is False
    assert all_retrieval_helpers_empty([]) is False, (
        "no retrieval helpers at all means the decision doesn't apply"
    )


def test_summary_alone_is_not_signal() -> None:
    """A standalone summary is NOT signal. Librarians often emit
    'no notes found' / 'checked vault' / similar self-describing
    summaries that carry zero answer content. Signal must be
    structured: hits, facts, or citations."""
    hrs = [
        _Hr(role="researcher", output={
            "hits": [],
            "summary": "Researcher executed 3 DDG queries; no facts "
                       "extracted.",
        }),
        _Hr(role="librarian", output={
            "hits": [],
            "summary": "Checked vault, no matching notes.",
        }),
    ]
    fab = "Toronto-Haneda flights run $1200-$1800."
    out = enforce_empty_retrieval_reply(fab, hrs)
    assert "couldn't find" in out.lower(), (
        "standalone summaries are not signal — guard must fire"
    )


def test_citations_only_counts_as_signal() -> None:
    """Researcher sometimes returns just citations (URLs) without a
    structured facts list. Still treat as signal — the synthesizer
    can ground claims on the cited pages even with no facts list."""
    hrs = [
        _Hr(role="researcher", output={
            "facts": [],
            "citations": ["https://example.com/x"],
        }),
        _Hr(role="librarian", output={"hits": []}),
    ]
    fab = "Some answer."
    out = enforce_empty_retrieval_reply(fab, hrs)
    assert out == fab, "citations are signal — no override"


def test_relevance_gated_summary_counts_as_empty() -> None:
    """The relevance gate sets `hits: []` when it blanks off-topic
    output. That empty-hits state should trigger the guard."""
    hrs = [
        _Hr(role="librarian", output={
            "summary": "librarian returned hits but none matched the "
                       "user's question — treating as empty",
            "hits": [],
        }),
        _Hr(role="researcher", output={"facts": [], "summary": ""}),
    ]
    fab = "Cafe Mikkeller Yoyogi closes at 23:00 on Wednesdays."
    out = enforce_empty_retrieval_reply(fab, hrs)
    assert "couldn't find" in out.lower(), (
        "post-relevance-gate empty hits should fire the guard"
    )
