"""Tests for vault_writer.wiki_synth (C3 — post-ingest wiki synthesis).

Four scenarios:
  (a) happy path with a related existing wiki page: a note produces
      wiki/<slug>.md with YAML frontmatter listing source ids and at least
      one [[wikilink]] when a related page is seeded.
  (b) log.md and index.md are updated after synthesis.
  (c) a seeded contradiction is returned in the result list (not silently
      applied — the page gets a warning callout instead of stating it as fact).
  (d) a synthesis exception does NOT propagate to the caller (fail-soft).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import pytest

from vault_writer.wiki_synth import SynthesisResult, _slugify, synthesize


# ------------------------------------------------------------------ fake helpers


class _FakeSearchResult:
    """Minimal duck-type matching vault_writer.index.SearchResult."""

    def __init__(self, path: str, body: str) -> None:
        self.path = path
        self.body = body
        self.note_type = "wiki"
        self.author = "test"
        self.audience = ["all"]
        self.frontmatter: dict = {}
        self.score = 0.9


def _make_fake_search(results: list[_FakeSearchResult]) -> Any:
    """Return a search_fn that always returns the given results."""

    def _search(query: str, k: int) -> list[_FakeSearchResult]:
        return results[:k]

    return _search


def _make_fake_llm(
    analyze_json: dict,
    generate_body: str,
) -> Any:
    """Return an llm_fn that returns canned responses.

    First call (ANALYZE step): returns JSON.
    Second call (GENERATE step): returns the article body.
    """
    calls: list[str] = []

    def _llm(system: str, user: str) -> str:
        calls.append(system)
        if len(calls) == 1:
            # ANALYZE step — return JSON
            return json.dumps(analyze_json)
        else:
            # GENERATE step — return article body
            return generate_body

    return _llm


def _make_failing_llm() -> Any:
    """Return an llm_fn that always raises."""

    def _llm(system: str, user: str) -> str:
        raise RuntimeError("Ollama is down")

    return _llm


# ------------------------------------------------------------------ slugify unit test


def test_slugify_basic() -> None:
    assert _slugify("Hive Gateway V3") == "hive-gateway-v3"
    assert _slugify("  hello  world  ") == "hello-world"
    assert _slugify("") == "untitled"


# ------------------------------------------------------------------ (a) happy path with related page


def test_synthesize_creates_wiki_page_with_wikilinks(tmp_path: Path) -> None:
    """A note with a related existing wiki page produces a page with [[wikilink]]."""
    vault = tmp_path / "vault"
    vault.mkdir()

    # Seed an existing wiki page in a sub-dir so search_fn can return it.
    wiki_dir = vault / "wiki"
    wiki_dir.mkdir()
    existing_slug = "hive-gateway"
    existing_page = wiki_dir / f"{existing_slug}.md"
    existing_page.write_text(
        "---\ntitle: Hive Gateway\ntype: wiki\n---\n\nThe gateway handles routing.\n",
        encoding="utf-8",
    )

    related_result = _FakeSearchResult(
        path=f"wiki/{existing_slug}.md",
        body="The gateway handles routing.",
    )
    search_fn = _make_fake_search([related_result])

    analyze_response = {
        "slug": "hive-gateway",
        "title": "Hive Gateway",
        "entities": ["gateway", "routing"],
        "related_slugs": ["hive-gateway"],
        "contradictions": [],
    }
    generate_body = (
        "The [[hive-gateway]] is responsible for routing requests between bots.\n"
        "It supports Ollama and provides load-balancing."
    )
    llm_fn = _make_fake_llm(analyze_response, generate_body)

    result = synthesize(
        "The Hive Gateway handles routing and load balancing.",
        note_id="knowledge/2026-06/hive-notes.md",
        search_fn=search_fn,
        llm_fn=llm_fn,
        vault_root=vault,
    )

    assert result.ok is True
    assert result.wiki_path is not None
    assert result.wiki_path.exists()
    assert result.contradictions == []

    content = result.wiki_path.read_text(encoding="utf-8")

    # Must have YAML frontmatter
    assert content.startswith("---\n")
    assert "title:" in content
    assert "sources:" in content
    assert "knowledge/2026-06/hive-notes.md" in content

    # Must have at least one [[wikilink]]
    assert "[[" in content and "]]" in content
    assert "[[hive-gateway]]" in content


# ------------------------------------------------------------------ (b) log.md + index.md updated


def test_synthesize_updates_log_and_index(tmp_path: Path) -> None:
    """After synthesis, wiki/log.md gets a new entry and wiki/index.md lists the page."""
    vault = tmp_path / "vault"
    vault.mkdir()

    search_fn = _make_fake_search([])
    analyze_response = {
        "slug": "sc-universe",
        "title": "Star Citizen Universe",
        "entities": ["universe"],
        "related_slugs": [],
        "contradictions": [],
    }
    generate_body = "A persistent universe MMO."
    llm_fn = _make_fake_llm(analyze_response, generate_body)

    result = synthesize(
        "Star Citizen is a space MMO set in the 30th century.",
        note_id="knowledge/2026-06/sc-note.md",
        search_fn=search_fn,
        llm_fn=llm_fn,
        vault_root=vault,
    )

    assert result.ok is True

    log_path = vault / "wiki" / "log.md"
    assert log_path.exists()
    log_content = log_path.read_text(encoding="utf-8")
    assert "sc-universe" in log_content
    assert "knowledge/2026-06/sc-note.md" in log_content

    index_path = vault / "wiki" / "index.md"
    assert index_path.exists()
    index_content = index_path.read_text(encoding="utf-8")
    assert "sc-universe" in index_content

    # Synthesize a second note — both pages should appear in the index.
    analyze_response2 = {
        "slug": "sc-ships",
        "title": "Star Citizen Ships",
        "entities": ["ships"],
        "related_slugs": [],
        "contradictions": [],
    }
    generate_body2 = "Ships are the primary vehicles in the game."
    llm_fn2 = _make_fake_llm(analyze_response2, generate_body2)

    result2 = synthesize(
        "There are many ship manufacturers in Star Citizen.",
        note_id="knowledge/2026-06/ships-note.md",
        search_fn=search_fn,
        llm_fn=llm_fn2,
        vault_root=vault,
    )
    assert result2.ok is True

    index_content2 = index_path.read_text(encoding="utf-8")
    assert "sc-universe" in index_content2
    assert "sc-ships" in index_content2


# ------------------------------------------------------------------ (c) contradiction returned, not silently applied


def test_synthesize_returns_contradictions_and_flags_in_page(tmp_path: Path) -> None:
    """When the LLM detects a contradiction it is returned in the result list
    and noted in the wiki page with a warning callout — NOT silently ignored or
    stated as fact."""
    vault = tmp_path / "vault"
    vault.mkdir()

    search_fn = _make_fake_search([])
    contradiction_text = "Note says port is 9000 but existing page says port is 8765."
    analyze_response = {
        "slug": "hive-config",
        "title": "Hive Config",
        "entities": ["port", "config"],
        "related_slugs": [],
        "contradictions": [contradiction_text],
    }
    # Generator should include the warning callout because contradictions were passed
    generate_body = (
        "The Hive daemon listens on a configurable port.\n\n"
        f"> **⚠ Contradiction detected**: {contradiction_text}"
    )
    llm_fn = _make_fake_llm(analyze_response, generate_body)

    result = synthesize(
        "The Hive daemon binds to port 9000 by default.",
        note_id="ops/hive-config.md",
        search_fn=search_fn,
        llm_fn=llm_fn,
        vault_root=vault,
    )

    assert result.ok is True
    assert len(result.contradictions) == 1
    assert contradiction_text in result.contradictions[0]

    # The page itself must contain the contradiction callout
    content = result.wiki_path.read_text(encoding="utf-8")
    assert "⚠" in content or "Contradiction" in content

    # Log entry must note the contradiction count
    log_content = (vault / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "1 contradiction" in log_content


# ------------------------------------------------------------------ (d) synthesis exception is fail-soft


def test_synthesize_exception_does_not_raise(tmp_path: Path) -> None:
    """A synthesis failure (LLM error) must return ok=False and NOT raise."""
    vault = tmp_path / "vault"
    vault.mkdir()

    search_fn = _make_fake_search([])
    llm_fn = _make_failing_llm()

    result = synthesize(
        "Some note body.",
        note_id="knowledge/some-note.md",
        search_fn=search_fn,
        llm_fn=llm_fn,
        vault_root=vault,
    )

    assert result.ok is False
    assert result.wiki_path is None
    assert result.error is not None
    assert "Ollama is down" in (result.error or "")

    # No wiki page was written
    wiki_dir = vault / "wiki"
    if wiki_dir.exists():
        pages = [p for p in wiki_dir.glob("*.md")
                 if p.name not in ("index.md", "log.md")]
        assert pages == [], f"unexpected wiki pages written on failure: {pages}"


# ------------------------------------------------------------------ (d2) search_fn failure is fail-soft


def test_synthesize_search_failure_is_graceful(tmp_path: Path) -> None:
    """A search_fn that raises must not break synthesis — it just proceeds without
    related pages."""
    vault = tmp_path / "vault"
    vault.mkdir()

    def _bad_search(query: str, k: int) -> list:
        raise RuntimeError("DB locked")

    analyze_response = {
        "slug": "resilience",
        "title": "Resilience",
        "entities": ["resilience"],
        "related_slugs": [],
        "contradictions": [],
    }
    generate_body = "Resilience is the ability to recover from failure."
    llm_fn = _make_fake_llm(analyze_response, generate_body)

    result = synthesize(
        "The system must be resilient.",
        note_id="ops/resilience.md",
        search_fn=_bad_search,
        llm_fn=llm_fn,
        vault_root=vault,
    )

    # Search failure should be swallowed; synthesis should succeed
    assert result.ok is True
    assert result.wiki_path is not None
    assert result.wiki_path.exists()


# ------------------------------------------------------------------ update path: existing page merges sources


def test_synthesize_update_merges_existing_sources(tmp_path: Path) -> None:
    """Writing the same slug twice must merge the sources list in the frontmatter."""
    vault = tmp_path / "vault"
    vault.mkdir()

    search_fn = _make_fake_search([])
    analyze_response = {
        "slug": "crew-system",
        "title": "Crew System",
        "entities": ["crew"],
        "related_slugs": [],
        "contradictions": [],
    }
    body1 = "The crew system manages ship personnel."
    body2 = "Crew members can be assigned to stations."

    llm_fn1 = _make_fake_llm(analyze_response, body1)
    llm_fn2 = _make_fake_llm(analyze_response, body2)

    r1 = synthesize(
        "Crew can be hired and assigned to ships.",
        note_id="knowledge/crew-note-1.md",
        search_fn=search_fn,
        llm_fn=llm_fn1,
        vault_root=vault,
    )
    assert r1.ok is True

    r2 = synthesize(
        "Crew members can man gunnery stations.",
        note_id="knowledge/crew-note-2.md",
        search_fn=search_fn,
        llm_fn=llm_fn2,
        vault_root=vault,
    )
    assert r2.ok is True

    # The page should now list both source note IDs
    content = r2.wiki_path.read_text(encoding="utf-8")
    assert "knowledge/crew-note-1.md" in content
    assert "knowledge/crew-note-2.md" in content
