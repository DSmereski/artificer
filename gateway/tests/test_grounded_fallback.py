"""Synth-timeout fallback should hand the user real retrieval content.

When the synthesizer times out / errors but retrieval helpers returned
signal, `HiveCoordinator._compose_fallback` must compose a grounded
reply from that content instead of the generic apology — without
leaking helper role names. When everything was empty it keeps the
canonical "couldn't find that" admission.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gateway.hallucination_guard import grounded_snippets_from_helpers
from gateway.hive_coordinator import HiveCoordinator


@dataclass
class _Hr:
    role: str
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    raw_text: str = ""


def _fallback(helper_results: list[_Hr]) -> str:
    # plan arg is unused by the fallback body; pass a placeholder.
    return HiveCoordinator._compose_fallback(_Hr(role="planner"), helper_results)


def test_all_empty_keeps_canonical_message() -> None:
    hrs = [
        _Hr(role="librarian", output={"hits": [], "summary": ""}),
        _Hr(role="researcher", output={"facts": [], "summary": ""}),
    ]
    out = _fallback(hrs)
    assert "couldn't find that" in out.lower()


def test_populated_helpers_compose_grounded_reply() -> None:
    hrs = [
        _Hr(
            role="librarian",
            output={
                "hits": [
                    {"path": "notes/x.md", "excerpt": "Port 2947 is the dev port."},
                    {"path": "notes/y.md", "excerpt": "Webpack flag is required."},
                ],
            },
        ),
    ]
    out = _fallback(hrs)
    assert "Port 2947 is the dev port" in out
    assert "Webpack flag is required" in out
    # No bulleted list markers — that's how the old leak surfaced.
    assert "\n- " not in out
    # Not the generic apology.
    assert "couldn't compose a clean reply" not in out.lower()


def test_grounded_reply_never_leaks_role_names() -> None:
    hrs = [
        _Hr(
            role="researcher",
            output={"facts": ["The release shipped on 2026-05-01."]},
            raw_text="researcher internal scratch",
        ),
    ]
    out = _fallback(hrs)
    assert "The release shipped on 2026-05-01." in out
    for role in ("librarian", "researcher", "planner", "synthesizer", "critic"):
        assert role not in out.lower()


def test_snippets_extractor_dedupes_and_clamps() -> None:
    hrs = [
        _Hr(role="librarian", output={"hits": [
            {"excerpt": "same fact"},
            {"excerpt": "same fact"},
            {"excerpt": "other fact"},
        ]}),
    ]
    snips = grounded_snippets_from_helpers(hrs, max_items=3)
    assert snips == ["same fact", "other fact"]


def test_snippets_extractor_skips_signalless_helpers() -> None:
    hrs = [
        _Hr(role="librarian", output={"hits": [], "summary": "no notes found"}),
    ]
    assert grounded_snippets_from_helpers(hrs) == []
