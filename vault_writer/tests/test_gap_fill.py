"""Tests for vault_writer.gap_fill (C5).

Covers:
  (a) no search backend → GapFillResult(ok=False, skipped_reason set).
  (b) topics generated (fake LLM) → confirm=False → no ingest.
  (c) confirm=True + fake search + fake learn_fn → results ingested.
  (d) declining (confirm=False) → nothing ingested.
  (e) LLM returns bad JSON → GapFillResult(ok=False, error set).
  (f) search failure is handled gracefully (partial results ok).
  (g) _parse_topics handles markdown-fenced JSON arrays.
"""

from __future__ import annotations

import json
from typing import Callable

import pytest

from vault_writer.gap_fill import GapFillResult, _parse_topics, gap_fill


# ------------------------------------------------------------------ fake helpers


def _make_fake_llm(topics: list[str]) -> Callable[[str, str], str]:
    """Return an llm_fn that always returns a JSON array of topics."""

    def _llm(system: str, user: str) -> str:
        return json.dumps(topics)

    return _llm


def _make_bad_llm() -> Callable[[str, str], str]:
    """Return an llm_fn that returns unparseable gibberish."""

    def _llm(system: str, user: str) -> str:
        return "Sorry, I cannot help with that."

    return _llm


def _make_fake_search(results: list[dict]) -> Callable:
    """Monkeypatch target that replaces _run_search."""
    return results


# ------------------------------------------------------------------ (a) no search backend


def test_gap_fill_no_backend_returns_skipped() -> None:
    result = gap_fill(
        "RSI ship manufacturer has no wiki page.",
        llm_fn=_make_fake_llm(["RSI ships Star Citizen", "RSI Aurora specs"]),
        search_backend="none",
    )
    assert result.ok is False
    assert result.skipped_reason is not None
    assert "backend" in result.skipped_reason.lower() or "configured" in result.skipped_reason.lower()
    assert result.ingested is False


# ------------------------------------------------------------------ (b) topics generated, confirm=False → no ingest


def test_gap_fill_confirm_false_no_ingest(monkeypatch) -> None:
    """confirm=False → topics generated, search run, but learn_fn never called."""
    import vault_writer.gap_fill as gf_mod

    search_calls: list[str] = []

    def _fake_run_search(query: str, *, backend: str, max_results: int = 3) -> list[dict]:
        search_calls.append(query)
        return [{"title": f"Result for {query}", "url": "https://example.com", "snippet": "Some text."}]

    monkeypatch.setattr(gf_mod, "_run_search", _fake_run_search)

    learn_calls: list[str] = []

    def _learn(title: str, body: str, url: str) -> None:
        learn_calls.append(title)

    result = gap_fill(
        "RSI ship manufacturer has no wiki page.",
        llm_fn=_make_fake_llm(["RSI ships Star Citizen", "RSI Aurora specs"]),
        confirm=False,
        learn_fn=_learn,
        search_backend="tavily",  # non-none but monkeypatched
    )

    assert result.ok is True
    assert len(result.topics) == 2
    assert len(result.search_results) > 0
    # confirm=False → learn_fn NEVER called
    assert learn_calls == []
    assert result.ingested is False
    assert result.skipped_reason is not None


# ------------------------------------------------------------------ (c) confirm=True + fake search → ingested


def test_gap_fill_confirm_true_ingests(monkeypatch) -> None:
    """confirm=True + search results → learn_fn called once per result."""
    import vault_writer.gap_fill as gf_mod

    def _fake_run_search(query: str, *, backend: str, max_results: int = 3) -> list[dict]:
        return [
            {"title": "Result A", "url": "https://example.com/a", "snippet": "Content A."},
            {"title": "Result B", "url": "https://example.com/b", "snippet": "Content B."},
        ]

    monkeypatch.setattr(gf_mod, "_run_search", _fake_run_search)

    learn_calls: list[tuple] = []

    def _learn(title: str, body: str, url: str) -> None:
        learn_calls.append((title, url))

    result = gap_fill(
        "RSI ship manufacturer has no wiki page.",
        llm_fn=_make_fake_llm(["RSI Aurora", "RSI Constellation"]),
        confirm=True,
        learn_fn=_learn,
        search_backend="searxng",  # monkeypatched
    )

    assert result.ok is True
    assert result.ingested is True
    # 2 topics × 2 results = 4 calls
    assert len(learn_calls) == 4
    # All calls use real search result data (not fences)
    for title, url in learn_calls:
        assert title in ("Result A", "Result B")
        assert url.startswith("https://")


# ------------------------------------------------------------------ (d) declining → nothing ingested


def test_gap_fill_confirm_false_explicitly_skips(monkeypatch) -> None:
    """Explicitly declining (confirm=False, no learn_fn) → nothing ingested."""
    import vault_writer.gap_fill as gf_mod

    monkeypatch.setattr(gf_mod, "_run_search", lambda q, *, backend, max_results=3: [
        {"title": "Hit", "url": "https://x.com", "snippet": "text"}
    ])

    result = gap_fill(
        "Some gap.",
        llm_fn=_make_fake_llm(["query one"]),
        confirm=False,
        learn_fn=None,
        search_backend="tavily",
    )

    assert result.ok is True
    assert result.ingested is False
    assert result.skipped_reason is not None


# ------------------------------------------------------------------ (e) bad LLM JSON → error


def test_gap_fill_bad_llm_json_returns_error(monkeypatch) -> None:
    import vault_writer.gap_fill as gf_mod

    monkeypatch.setattr(gf_mod, "_run_search", lambda q, *, backend, max_results=3: [])

    result = gap_fill(
        "Some gap.",
        llm_fn=_make_bad_llm(),
        search_backend="tavily",
    )

    assert result.ok is False
    assert result.error is not None
    assert "topics" in result.error.lower() or result.topics == []


# ------------------------------------------------------------------ (f) search failure graceful


def test_gap_fill_search_failure_returns_empty_results(monkeypatch) -> None:
    """If search raises, gap_fill returns empty results but ok=True (with skipped_reason)."""
    import vault_writer.gap_fill as gf_mod

    def _failing_search(query: str, *, backend: str, max_results: int = 3) -> list[dict]:
        raise RuntimeError("network error")

    monkeypatch.setattr(gf_mod, "_run_search", _failing_search)

    learn_calls: list = []

    result = gap_fill(
        "Some gap.",
        llm_fn=_make_fake_llm(["query one", "query two"]),
        confirm=False,
        learn_fn=lambda t, b, u: learn_calls.append(t),
        search_backend="tavily",
    )

    # Topics were generated, search failed but was swallowed, no ingest
    assert result.topics == ["query one", "query two"]
    assert result.search_results == []
    assert learn_calls == []


# ------------------------------------------------------------------ (g) _parse_topics helper


def test_parse_topics_plain_array() -> None:
    assert _parse_topics('["query one", "query two"]') == ["query one", "query two"]


def test_parse_topics_markdown_fenced() -> None:
    raw = "```json\n[\"query one\", \"query two\"]\n```"
    assert _parse_topics(raw) == ["query one", "query two"]


def test_parse_topics_extra_prose() -> None:
    raw = "Here are your topics:\n[\"query one\", \"query two\"]\nThank you."
    assert _parse_topics(raw) == ["query one", "query two"]


def test_parse_topics_gibberish_returns_empty() -> None:
    assert _parse_topics("I cannot help with that.") == []


def test_parse_topics_filters_empty_strings() -> None:
    assert _parse_topics('["query one", "", "query two"]') == ["query one", "query two"]


# ------------------------------------------------------------------ (h) confirm=True + no learn_fn


def test_gap_fill_confirm_true_no_learn_fn_skipped(monkeypatch) -> None:
    """confirm=True but learn_fn=None → skipped, not ingested."""
    import vault_writer.gap_fill as gf_mod

    monkeypatch.setattr(gf_mod, "_run_search", lambda q, *, backend, max_results=3: [
        {"title": "Hit", "url": "https://x.com", "snippet": "text"}
    ])

    result = gap_fill(
        "Some gap.",
        llm_fn=_make_fake_llm(["query one"]),
        confirm=True,
        learn_fn=None,
        search_backend="searxng",
    )

    assert result.ok is True
    assert result.ingested is False
    assert result.skipped_reason is not None
    assert "learn_fn" in result.skipped_reason
