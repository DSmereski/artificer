"""Tests for the relevance gate that filters off-topic retrieval-helper
outputs before synthesizer prompt assembly.

Regression target: 04-27 production case where librarian returned a
17th-century-dagger note for a "Drake Cutlass Black" question and
the synthesizer rendered it verbatim (Rule 8b prompt-level guard
wasn't reliable on its own)."""

from __future__ import annotations

from gateway.helpers.base import HelperResult
from gateway.helpers.relevance_gate import filter_irrelevant


def _hr(role: str, output: dict, *, error: str | None = None) -> HelperResult:
    return HelperResult(role=role, model_id="m", output=output, error=error)


def test_librarian_off_topic_output_is_blanked():
    """Drake Cutlass Black query + a 17th-century-dagger librarian
    hit must be blanked: the synthesizer should see an empty
    librarian result and fall into Rule 8 ("admit ignorance")."""
    user_msg = "what are the specs of the Drake Cutlass Black?"
    hr = _hr("librarian", {
        "summary": "17th-century dagger of historical interest",
        "hits": [{
            "path": "weapons/historical-dagger.md",
            "excerpt": "a slender weapon used in melee combat",
        }],
    })
    out = filter_irrelevant(user_msg, [hr])
    assert len(out) == 1
    assert out[0].role == "librarian"
    assert out[0].output.get("hits") == []
    assert out[0].confidence == "low"


def test_librarian_relevant_output_passes_through():
    """A librarian hit that shares a salient token (e.g. 'cutlass')
    with the query must NOT be filtered."""
    user_msg = "what are the specs of the Drake Cutlass Black?"
    hr = _hr("librarian", {
        "summary": "Drake Cutlass Black ship specs (canonical RSI data)",
        "hits": [{
            "path": "ships/drake-cutlass-black.md",
            "excerpt": "Drake Cutlass Black is a militarized variant...",
        }],
    })
    out = filter_irrelevant(user_msg, [hr])
    assert out[0].output == hr.output
    assert out[0].confidence == "medium"  # default, untouched


def test_researcher_off_topic_facts_are_blanked():
    user_msg = "tell me about kraken sightings off the Norwegian coast"
    hr = _hr("researcher", {
        "summary": "Bose QC45 headphones product listing",
        "facts": ["The QC45 supports Bluetooth multipoint pairing"],
    })
    out = filter_irrelevant(user_msg, [hr])
    assert out[0].output.get("hits") == []
    assert out[0].confidence == "low"


def test_chat_recall_is_not_gated():
    """chat_recall returning ("hello") for a "what are kraken specs"
    query must pass through — the recall helper indexes prior turns
    by exact text and a token-overlap gate would falsely silence
    legitimately-recalled small-talk."""
    user_msg = "what are kraken specs"
    hr = _hr("chat_recall", {
        "summary": "earlier you asked about Drake ships",
        "hits": [{"role": "user", "content": "hello"}],
    })
    out = filter_irrelevant(user_msg, [hr])
    assert out[0].output == hr.output


def test_errored_helper_passes_through_unchanged():
    user_msg = "what are the specs of the Drake Cutlass Black?"
    hr = _hr("librarian", {}, error="timeout")
    out = filter_irrelevant(user_msg, [hr])
    assert out[0].error == "timeout"


def test_query_with_no_salient_tokens_skips_gate():
    """Queries like 'and so?' have no salient tokens — we cannot
    compute overlap, so don't filter (avoid false negatives)."""
    user_msg = "and so?"
    hr = _hr("librarian", {
        "summary": "anything at all",
        "hits": [{"path": "x.md", "excerpt": "some content"}],
    })
    out = filter_irrelevant(user_msg, [hr])
    assert out[0].output == hr.output


def test_short_acronyms_count_as_salient_tokens():
    """Regression for scenario 10 v3 turn 7 (2026-05-02): query
    "What's most likely now — GPU, RAM, or PSU?" tokenized to only
    {'likely','most'} because GPU/RAM/PSU are 3 chars and dropped
    by the length filter. A librarian hit whose only overlap is the
    acronym must NOT be blanked."""
    user_msg = "What's most likely now — GPU, RAM, or PSU?"
    hr = _hr("librarian", {
        "summary": "GPU diagnostics: reseat the card and check power",
        "hits": [{
            "path": "hardware/gpu-troubleshooting.md",
            "excerpt": "PSU rails feeding the GPU should read 12V steady",
        }],
    })
    out = filter_irrelevant(user_msg, [hr])
    assert out[0].output == hr.output, (
        "acronym GPU/PSU overlap should keep librarian output"
    )
    assert out[0].confidence == "medium"


def test_lowercase_three_letter_words_still_dropped():
    """The acronym fix must not regress the original guard against
    common 3-letter words. Lowercase "ram" (the verb) does NOT
    qualify as an acronym; output mentioning only "ram" lowercase
    against a query about something else should still be gated."""
    user_msg = "tell me about kraken sightings"
    hr = _hr("librarian", {
        "summary": "you can ram the gate open if needed",
        "hits": [{"path": "x.md", "excerpt": "the door will ram shut"}],
    })
    out = filter_irrelevant(user_msg, [hr])
    assert out[0].output.get("hits") == []
    assert out[0].confidence == "low"


def test_filter_does_not_mutate_input():
    user_msg = "what are the specs of the Drake Cutlass Black?"
    hr = _hr("librarian", {
        "summary": "17th-century dagger",
        "hits": [{"path": "weapons/dagger.md"}],
    })
    original_summary = hr.output["summary"]
    filter_irrelevant(user_msg, [hr])
    assert hr.output["summary"] == original_summary
    assert hr.confidence == "medium"


def test_notes_field_contributes_to_overlap_check():
    """Finding 3: _output_text ignored the `notes` key, so a helper
    that returned topic-relevant text exclusively in `notes` was
    incorrectly blanked by the relevance gate.

    Scenario: librarian returns a result with a `notes` field that
    shares salient tokens with the query (e.g. "cutlass", "drake").
    Without the fix, output_tokens comes back empty, overlap=zero and
    the result is gated. With the fix, notes tokens participate in the
    overlap check and the result passes through.
    """
    user_msg = "what are the specs of the Drake Cutlass Black?"
    hr = _hr("librarian", {
        # summary is empty — relevance signal lives only in notes.
        "summary": "",
        "notes": "Drake Cutlass Black is a militarized variant of the Cutlass series.",
    })
    out = filter_irrelevant(user_msg, [hr])
    # Notes carry matching tokens — must NOT be blanked.
    assert out[0].output == hr.output, (
        "notes field overlap should keep librarian output un-gated"
    )
    assert out[0].confidence == "medium"


def test_notes_only_payload_irrelevant_is_still_blanked():
    """Symmetry test: a notes field that shares zero salient tokens
    with the query still gets blanked. The notes field is included in
    the overlap check but doesn't disable the gate for truly irrelevant
    results.
    """
    user_msg = "what are the specs of the Drake Cutlass Black?"
    hr = _hr("librarian", {
        "summary": "",
        "notes": "The history of medieval tapestries in France.",
    })
    out = filter_irrelevant(user_msg, [hr])
    assert out[0].output.get("hits") == [], (
        "off-topic notes-only payload should still be gated"
    )
    assert out[0].confidence == "low"
