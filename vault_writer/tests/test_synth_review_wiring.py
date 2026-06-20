"""Tests for wiki_synth -> review_queue wiring (C4).

When review_conn is passed to synthesize() and the LLM detects
contradictions or gaps, rows must be inserted into wiki_reviews.

Covers:
  (a) contradiction detected -> row in wiki_reviews with kind='contradiction'.
  (b) gap detected -> row in wiki_reviews with kind='gap'.
  (c) multiple items -> multiple rows.
  (d) no issues -> no rows written.
  (e) review_conn=None -> no rows, synthesis still completes (existing tests).
  (f) review_queue write failure is fail-soft (synthesis completes).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from vault_writer.review_queue import ensure_schema, get_open_reviews
from vault_writer.wiki_synth import SynthesisResult, synthesize


# ------------------------------------------------------------------ fake helpers


def _make_fake_llm(analyze_json: dict, generate_body: str):
    calls: list[str] = []

    def _llm(system: str, user: str) -> str:
        calls.append(system)
        if len(calls) == 1:
            return json.dumps(analyze_json)
        return generate_body

    return _llm


def _make_fake_search():
    def _search(query: str, k: int):
        return []

    return _search


# ------------------------------------------------------------------ (a) contradiction -> row


def test_synth_contradiction_writes_review_row(tmp_path: Path) -> None:
    """A seeded contradiction must produce a wiki_reviews row with kind='contradiction'."""
    vault = tmp_path / "vault"
    vault.mkdir()

    db = sqlite3.connect(":memory:")
    ensure_schema(db)

    analyze = {
        "slug": "gateway-port",
        "title": "Gateway Port",
        "entities": ["port"],
        "related_slugs": [],
        "contradictions": ["Note says port 9000 but wiki says 8765."],
        "gaps": [],
    }
    llm_fn = _make_fake_llm(analyze, "The gateway daemon binds to a configurable port.")

    result = synthesize(
        "The gateway daemon binds to port 9000.",
        note_id="ops/gateway.md",
        search_fn=_make_fake_search(),
        llm_fn=llm_fn,
        vault_root=vault,
        review_conn=db,
    )

    assert result.ok is True
    assert result.reviews_queued == 1

    reviews = get_open_reviews(db)
    assert len(reviews) == 1
    r = reviews[0]
    assert r["kind"] == "contradiction"
    assert "9000" in r["summary"]
    assert "ops/gateway.md" in r["source_notes"]
    assert r["slug"] == "gateway-port"


# ------------------------------------------------------------------ (b) gap -> row


def test_synth_gap_writes_review_row(tmp_path: Path) -> None:
    """A detected gap must produce a wiki_reviews row with kind='gap'."""
    vault = tmp_path / "vault"
    vault.mkdir()

    db = sqlite3.connect(":memory:")
    ensure_schema(db)

    analyze = {
        "slug": "topic-ships",
        "title": "Topic Ships",
        "entities": ["ships", "manufacturers"],
        "related_slugs": [],
        "contradictions": [],
        "gaps": ["Ship manufacturer has no wiki page."],
    }
    llm_fn = _make_fake_llm(analyze, "Ships span multiple manufacturers.")

    result = synthesize(
        "The Aurora and Constellation are popular ships.",
        note_id="knowledge/ships.md",
        search_fn=_make_fake_search(),
        llm_fn=llm_fn,
        vault_root=vault,
        review_conn=db,
    )

    assert result.ok is True
    assert result.reviews_queued == 1

    reviews = get_open_reviews(db)
    assert len(reviews) == 1
    r = reviews[0]
    assert r["kind"] == "gap"
    assert "manufacturer" in r["summary"]


# ------------------------------------------------------------------ (c) multiple items -> multiple rows


def test_synth_multiple_contradictions_and_gaps(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()

    db = sqlite3.connect(":memory:")
    ensure_schema(db)

    analyze = {
        "slug": "mixed-review",
        "title": "Mixed",
        "entities": [],
        "related_slugs": [],
        "contradictions": ["Contradiction A", "Contradiction B"],
        "gaps": ["Gap X", "Gap Y"],
    }
    llm_fn = _make_fake_llm(analyze, "Article body.")

    result = synthesize(
        "Some note text.",
        note_id="knowledge/note.md",
        search_fn=_make_fake_search(),
        llm_fn=llm_fn,
        vault_root=vault,
        review_conn=db,
    )

    assert result.ok is True
    assert result.reviews_queued == 4  # 2 contradictions + 2 gaps

    reviews = get_open_reviews(db)
    assert len(reviews) == 4
    kinds = {r["kind"] for r in reviews}
    assert kinds == {"contradiction", "gap"}


# ------------------------------------------------------------------ (d) no issues -> no rows


def test_synth_no_issues_no_rows(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()

    db = sqlite3.connect(":memory:")
    ensure_schema(db)

    analyze = {
        "slug": "clean",
        "title": "Clean Article",
        "entities": [],
        "related_slugs": [],
        "contradictions": [],
        "gaps": [],
    }
    llm_fn = _make_fake_llm(analyze, "No issues here.")

    result = synthesize(
        "Everything is consistent.",
        note_id="knowledge/clean.md",
        search_fn=_make_fake_search(),
        llm_fn=llm_fn,
        vault_root=vault,
        review_conn=db,
    )

    assert result.ok is True
    assert result.reviews_queued == 0
    assert get_open_reviews(db) == []


# ------------------------------------------------------------------ (e) review_conn=None -> no rows, synth ok


def test_synth_no_review_conn_still_ok(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()

    analyze = {
        "slug": "test",
        "title": "Test",
        "entities": [],
        "related_slugs": [],
        "contradictions": ["A contradiction"],
        "gaps": [],
    }
    llm_fn = _make_fake_llm(analyze, "Article body.")

    result = synthesize(
        "Some note.",
        note_id="knowledge/test.md",
        search_fn=_make_fake_search(),
        llm_fn=llm_fn,
        vault_root=vault,
        review_conn=None,  # no review_conn
    )

    assert result.ok is True
    # contradictions still returned in result
    assert len(result.contradictions) == 1
    # reviews_queued is 0 because no conn was supplied
    assert result.reviews_queued == 0


# ------------------------------------------------------------------ (f) review_queue failure is fail-soft


def test_synth_review_queue_failure_does_not_abort(tmp_path: Path, monkeypatch) -> None:
    """If the review_queue write fails, synthesis must still complete."""
    vault = tmp_path / "vault"
    vault.mkdir()

    db = sqlite3.connect(":memory:")
    ensure_schema(db)

    # Make add_review raise
    import vault_writer.review_queue as rq_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("DB write failed")

    monkeypatch.setattr(rq_mod, "add_review", _boom)

    analyze = {
        "slug": "failsafe",
        "title": "Failsafe",
        "entities": [],
        "related_slugs": [],
        "contradictions": ["A contradiction"],
        "gaps": [],
    }
    llm_fn = _make_fake_llm(analyze, "Article body.")

    result = synthesize(
        "Triggering a review write failure.",
        note_id="knowledge/failsafe.md",
        search_fn=_make_fake_search(),
        llm_fn=llm_fn,
        vault_root=vault,
        review_conn=db,
    )

    # Synthesis must still succeed even though review_queue write failed
    assert result.ok is True
    assert result.wiki_path is not None
    assert result.wiki_path.exists()
