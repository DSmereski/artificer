# vault_writer/groomer/tests/test_suggestion.py
"""Tests for the Suggestion dataclass + scanner registry."""
from __future__ import annotations

import pytest

from vault_writer.groomer.suggestion import (
    KINDS,
    REGISTRY,
    Confidence,
    Suggestion,
    label_for,
)


def test_suggestion_known_kind_ok() -> None:
    s = Suggestion(
        kind="dup_scanner",
        slug="penguin",
        confidence=0.94,
        title="Possible duplicate",
        body_md="...",
    )
    assert s.kind == "dup_scanner"
    assert s.confidence == 0.94


def test_suggestion_unknown_kind_rejected() -> None:
    with pytest.raises(ValueError, match="unknown kind"):
        Suggestion(
            kind="not_a_scanner",
            slug="x",
            confidence=0.5,
            title="t",
            body_md="b",
        )


def test_suggestion_confidence_clamped() -> None:
    with pytest.raises(ValueError):
        Suggestion(kind="dup_scanner", slug="x", confidence=1.5,
                   title="t", body_md="b")
    with pytest.raises(ValueError):
        Suggestion(kind="dup_scanner", slug="x", confidence=-0.1,
                   title="t", body_md="b")


def test_confidence_levels() -> None:
    assert Confidence.LOW < Confidence.MEDIUM < Confidence.HIGH


def test_registry_is_single_source() -> None:
    # Every KIND must have a label, and label_for must be a closed total fn.
    assert set(REGISTRY) == set(KINDS)
    for kind in KINDS:
        assert label_for(kind)  # non-empty label
    with pytest.raises(KeyError):
        label_for("not_a_scanner")
