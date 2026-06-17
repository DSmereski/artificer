"""Tests for the vault write quality gate."""

from __future__ import annotations

from gateway.vault_quality import evaluate


_GOOD_BODY = (
    "This note carries enough informative tokens about a topic to "
    "satisfy the quality gate without tripping the link-list filter."
)


def test_accepts_meaty_note() -> None:
    v = evaluate(title="Drake Cutlass", body=_GOOD_BODY, category="knowledge")
    assert v.ok, v.reason


def test_rejects_empty_body() -> None:
    v = evaluate(title="Topic", body="", category="knowledge")
    assert not v.ok
    assert "body too short" in v.reason


def test_rejects_short_stub() -> None:
    v = evaluate(title="Topic", body="The Kraken is a ship.", category="knowledge")
    assert not v.ok
    assert "body too short" in v.reason


def test_rejects_titleless_note() -> None:
    """Single-char titles count as no-tokens — gate fails."""
    v = evaluate(title="x", body=_GOOD_BODY, category="knowledge")
    assert not v.ok
    assert "title" in v.reason


def test_rejects_link_list() -> None:
    body = (
        "Sources:\n"
        "- https://example.com/a\n"
        "- https://example.com/b\n"
        "- https://example.com/c\n"
        "- https://example.com/d\n"
        "- https://example.com/e\n"
    )
    v = evaluate(title="Sources", body=body, category="knowledge")
    assert not v.ok
    assert "informative tokens" in v.reason or "informative-content ratio" in v.reason


def test_journal_category_exempt_from_short_body() -> None:
    """Journal entries can be short — the gate skips them."""
    v = evaluate(title="Today", body="Short jot.", category="journal")
    assert v.ok


def test_session_exempt() -> None:
    v = evaluate(title="Daily Check", body="x", category="session")
    assert v.ok


def test_reference_exempt() -> None:
    """Recipe / reference notes carry value in their structured extras."""
    v = evaluate(title="Image Recipe X", body="x", category="reference")
    assert v.ok


def test_score_returned_for_passing_note() -> None:
    v = evaluate(title="Topic", body=_GOOD_BODY, category="knowledge")
    assert v.ok
    assert v.score is not None
    assert v.score["body_chars"] >= 80
