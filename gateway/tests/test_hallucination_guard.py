"""Tests for `gateway.hallucination_guard.strip_hallucinated_sentences`.

Pins the two-stage filter against the regressions it was built to
catch (2026-04-28 turn-log review):

  - **Action-claim guard.** "I saved that to your vault" must drop
    when no `vault_learn` was emitted, but pass when one was.
    "Smart-linked the related pages" must drop when no `vault_learn`
    was emitted (auto-linking lives inside vault_learn).
  - **Number guard.** Specific numbers (≥3 digits) must trace into
    the helper-output haystack, OR the sentence is dropped. Bare
    small numbers ("a couple", "one of three") are exempt.
  - **Refusal exemption.** Safety/HIPAA/911/rate-limit content
    passes regardless of number tracing.
"""

from __future__ import annotations

from dataclasses import dataclass

from gateway.hallucination_guard import strip_hallucinated_sentences


@dataclass
class _FakeHelperResult:
    raw_text: str = ""
    output: dict | None = None


# ---------------------------------------------------------------- action-claim


def test_strip_drops_save_claim_without_vault_learn():
    helpers = [_FakeHelperResult(raw_text="background facts")]
    reply = "I saved that to your vault. The launch was great."
    out = strip_hallucinated_sentences(reply, helpers, actions=[])
    assert "saved that to your vault" not in out
    assert "launch was great" in out


def test_strip_keeps_save_claim_with_vault_learn():
    helpers = [_FakeHelperResult(raw_text="background facts")]
    reply = "I saved that to your vault. Done."
    actions = [{"verb": "vault_learn", "payload": {}}]
    out = strip_hallucinated_sentences(reply, helpers, actions=actions)
    assert "saved that to your vault" in out


def test_strip_drops_smartlink_claim_without_vault_learn():
    """The `_autolink_body` helper is part of vault_learn, so smart-link
    claims need a vault_learn in the action list."""
    reply = "Smart-linked the related pages. Anything else?"
    out = strip_hallucinated_sentences(reply, [], actions=[])
    assert "smart-linked" not in out.lower()
    assert "anything else" in out.lower()


# ---------------------------------------------------------------- number guard


def test_strip_drops_untraced_specific_number():
    """Use a 2-sentence reply so the fallback ('don't return blank')
    doesn't mask the number-guard drop."""
    helpers = [_FakeHelperResult(raw_text="The cargo capacity is 188.")]
    reply = "The cargo capacity is 226 SCU. Anyway."
    out = strip_hallucinated_sentences(reply, helpers, actions=None)
    assert "226" not in out
    assert "Anyway" in out


def test_strip_keeps_traced_specific_number():
    helpers = [_FakeHelperResult(raw_text="The cargo capacity is 188.")]
    reply = "The cargo capacity is 188 SCU."
    out = strip_hallucinated_sentences(reply, helpers, actions=None)
    assert "188" in out


def test_strip_keeps_bare_small_numbers():
    """Two-digit / one-digit numbers are too generic to flag."""
    reply = "There are 3 ways to do this. One of them is faster."
    out = strip_hallucinated_sentences(reply, [], actions=None)
    assert "3 ways" in out
    assert "one of them" in out.lower()


def test_strip_normalises_thousands_separator():
    """'1,200' in the reply should match '1200' in the helper output."""
    helpers = [_FakeHelperResult(raw_text="Top speed: 1200 m/s")]
    reply = "Top speed is 1,200 m/s."
    out = strip_hallucinated_sentences(reply, helpers, actions=None)
    assert "1,200" in out


def test_strip_exempts_refusal_content():
    """Even if numbers don't trace, refusal/safety messages must pass."""
    reply = "I won't share that — call 911 if it's an emergency."
    out = strip_hallucinated_sentences(reply, [], actions=None)
    assert "911" in out


# ---------------------------------------------------------------- edge cases


def test_strip_returns_empty_on_high_confidence_drops():
    """Action-claim + helper-run claim drops are high-confidence: the
    synth verifiably lied about doing something it didn't do. Better
    blank than a confidently-wrong reply — the coordinator substitutes
    `_compose_fallback` when the strip returns empty."""
    reply = "I saved that to your vault."
    out = strip_hallucinated_sentences(reply, [], actions=[])
    assert out == "", (
        "vault_learn claim with no emitted action -> high-confidence "
        "drop -> empty reply (downstream substitutes a fallback)"
    )


def test_strip_handles_empty_input():
    assert strip_hallucinated_sentences("", [], actions=None) == ""


def test_strip_uses_helper_dict_output_as_haystack():
    """`output` dicts also count toward the trace haystack."""
    helpers = [_FakeHelperResult(output={"value": 4242, "name": "thing"})]
    reply = "The value is 4242."
    out = strip_hallucinated_sentences(reply, helpers, actions=None)
    assert "4242" in out


# ---------------------------------------------------------------- sc-kb run regressions

def test_drops_saved_under_category_phrasing():
    """Surfaced 2026-06-02 SC knowledge-base run: synth said
    "I've saved a top-level note titled 'X' under the `knowledge`
    category" with actions=[] — pure hallucination. Must drop."""
    from gateway.hallucination_guard import strip_hallucinated_sentences
    reply = (
        "Here is the overview. I've saved a top-level note titled "
        "'Star Citizen — overview' under the `knowledge` category."
    )
    out = strip_hallucinated_sentences(reply, helper_results=[], actions=[])
    assert "saved a top-level note" not in out


def test_drops_saved_the_note_phrasing():
    """SC run: "Saved the UEE lore note to the vault under the
    'knowledge' category." — actions=[]. Must drop."""
    from gateway.hallucination_guard import strip_hallucinated_sentences
    reply = (
        "Saved the UEE lore note to the vault under the 'knowledge' "
        "category. It now covers the dominant human government."
    )
    out = strip_hallucinated_sentences(reply, helper_results=[], actions=[])
    assert "Saved the UEE lore note" not in out


def test_keeps_save_claim_when_vault_learn_emitted():
    """A real vault_learn action makes the claim truthful — guard
    must not strip it."""
    from gateway.hallucination_guard import strip_hallucinated_sentences
    reply = "Saved the UEE lore note to the vault under the 'knowledge' category."
    actions = [{"verb": "vault_learn", "payload": {"title": "x"}}]
    out = strip_hallucinated_sentences(reply, helper_results=[], actions=actions)
    assert "Saved" in out


# ---------------------------------------------------------------- passive-voice claims

def test_drops_has_been_saved_as_phrasing():
    """SC run 2026-06-02: 'The note has been saved as Stanton system.md
    with the slug stanton-system.' actions=[]. Must drop."""
    from gateway.hallucination_guard import strip_hallucinated_sentences
    reply = "The note has been saved as `Stanton system.md` with the slug `stanton-system`."
    out = strip_hallucinated_sentences(reply, helper_results=[], actions=[])
    assert "has been saved" not in out


def test_drops_was_added_to_knowledge_base():
    from gateway.hallucination_guard import strip_hallucinated_sentences
    reply = "This entry was added to your knowledge base."
    out = strip_hallucinated_sentences(reply, helper_results=[], actions=[])
    assert "was added" not in out


def test_keeps_passive_save_when_vault_learn_fired():
    from gateway.hallucination_guard import strip_hallucinated_sentences
    reply = "The note has been saved as `Stanton system.md`."
    actions = [{"verb": "vault_learn"}]
    out = strip_hallucinated_sentences(reply, helper_results=[], actions=actions)
    assert "has been saved" in out


# ---------------------------------------------------------------- groundedness guard

def test_groundedness_replaces_preamble_when_hits_unused():
    """SC retrieval eval (2026-06-05): synth wraps vault hits in fluffy
    preamble ('Based on the vault search results, here is...') without
    surfacing any concrete entity. Replace with grounded snippets."""
    from gateway.hallucination_guard import enforce_groundedness_with_hits

    @dataclass
    class FakeR:
        role: str = "librarian"
        output: dict | None = None
        error: str | None = None

    helpers = [FakeR(
        role="librarian",
        output={
            "summary": "found drake notes",
            "hits": [
                {"body": "Drake Interplanetary: Cutlass, Caterpillar, Buccaneer, Vulture, Kraken, Herald."},
                {"body": "Vulture is a Drake salvage ship."},
            ],
        },
    )]
    reply = (
        "Based on the vault search results, here is Hive's reply "
        "regarding Drake's lineup. Drake Interplanetary focuses on "
        "interstellar exploration and high-end luxury craft."
    )
    out = enforce_groundedness_with_hits(
        reply, helpers, user_msg="Tell me about Drake Interplanetary ships.",
    )
    assert "Cutlass" in out or "Vulture" in out, (
        "expected grounded fallback to surface entities from hits"
    )
    assert "interstellar exploration" not in out


def test_groundedness_preserves_reply_when_overlap_high():
    """When the reply already cites multiple hit entities, leave it alone."""
    from gateway.hallucination_guard import enforce_groundedness_with_hits

    @dataclass
    class FakeR:
        role: str = "librarian"
        output: dict | None = None
        error: str | None = None

    helpers = [FakeR(
        role="librarian",
        output={
            "summary": "found drake notes",
            "hits": [
                {"body": "Drake Interplanetary makes Cutlass, Caterpillar, Vulture, Kraken."},
            ],
        },
    )]
    reply = "Drake makes the Cutlass, Vulture, and Kraken among others."
    out = enforce_groundedness_with_hits(reply, helpers)
    assert out == reply


def test_groundedness_skips_when_no_retrieval_helpers():
    from gateway.hallucination_guard import enforce_groundedness_with_hits
    reply = "Based on context, here is a generic answer."
    assert enforce_groundedness_with_hits(reply, []) == reply
