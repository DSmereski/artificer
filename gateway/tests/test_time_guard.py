"""Time-of-day guard for the hallucination strip.

HH:MM tokens escape the number-guard's 3-char specificity threshold
(each side is at most 2 digits), so a synth that fabricates business
hours -- 'open 9:30 AM until 5:00 PM' -- used to pass through. The
time guard catches these as standalone tokens and requires each one
to trace to a helper haystack.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gateway.hallucination_guard import strip_hallucinated_sentences


@dataclass
class _Hr:
    role: str
    output: dict[str, Any] | None = None
    error: str | None = None
    raw_text: str = ""


def test_drops_untraced_time_in_sentence() -> None:
    reply = "Cafe Mikkeller closes at 11:30 PM on Wednesdays."
    hrs = [_Hr(role="researcher", output={
        "facts": [{"claim": "Tokyo cafe scene overview", "text": "various"}],
    })]
    out = strip_hallucinated_sentences(reply, hrs, actions=[])
    assert "11:30" not in out, (
        "time '11:30 PM' isn't in the researcher haystack — must be "
        "dropped"
    )


def test_keeps_traced_time_in_sentence() -> None:
    reply = "The cafe opens at 9:30 AM on weekdays."
    hrs = [_Hr(role="researcher", output={
        "facts": [{"claim": "weekday hours 9:30 AM - 5:00 PM"}],
    })]
    out = strip_hallucinated_sentences(reply, hrs, actions=[])
    assert "9:30" in out, "9:30 traces to the haystack -- must stay"


def test_format_variants_normalise() -> None:
    """'9:30 AM' / '9:30am' / '09:30am' should all match the same
    haystack token after normalisation."""
    reply = "Open from 09:30am."
    hrs = [_Hr(role="researcher", output={
        "facts": [{"text": "weekday hours 9:30 AM - 5:00 PM"}],
    })]
    out = strip_hallucinated_sentences(reply, hrs, actions=[])
    assert "09:30" in out or "9:30" in out, (
        "normalised form should match the haystack regardless of "
        "leading zero / space / case"
    )


def test_24h_time_format_handled() -> None:
    """'23:00' (24-hour) appearing in reply but not in haystack must
    be dropped."""
    reply = "The bar serves last call at 23:00."
    hrs = [_Hr(role="librarian", output={"hits": [
        {"path": "x.md", "excerpt": "open daily, no time data"},
    ]})]
    out = strip_hallucinated_sentences(reply, hrs, actions=[])
    assert "23:00" not in out


def test_time_refusal_exception_holds() -> None:
    """Refusal/safety sentences that happen to contain time tokens
    pass through (per the existing refusal exemption)."""
    reply = "Call 911 at 11:30 PM if you're in danger — do not delay."
    hrs = [_Hr(role="researcher", output={"facts": []})]
    out = strip_hallucinated_sentences(reply, hrs, actions=[])
    assert "11:30" in out, "refusal/safety language must NOT be filtered"


def test_multiple_times_one_untraced_drops_whole_sentence() -> None:
    """A sentence with two times where one traces and one doesn't is
    still entirely fabricated — drop it."""
    reply = "Open from 9:30 AM until 11:30 PM every day."
    hrs = [_Hr(role="researcher", output={
        "facts": [{"text": "9:30 AM opening only"}],
    })]
    out = strip_hallucinated_sentences(reply, hrs, actions=[])
    assert "11:30" not in out, "one untraced time -> drop the sentence"
