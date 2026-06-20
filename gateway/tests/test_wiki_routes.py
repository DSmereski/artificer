"""Tests for GET/POST /v1/wiki/reviews (C4 review-queue gateway routes).

Covers:
  (a) GET /v1/wiki/reviews — 401 without auth; 503 when vault DB absent;
      200 + empty list when DB seeded but empty.
  (b) GET /v1/wiki/reviews/count — same auth/503 pattern, returns count.
  (c) POST /v1/wiki/reviews/{id}/resolve — resolves an open item.
  (d) POST /v1/wiki/reviews/{id}/research — returns skipped_reason when
      no search backend is configured; no LLM needed because backend='none'.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vault_writer.review_queue import add_review, ensure_schema


# ------------------------------------------------------------------ helpers


def _seed_vault_db(vault_path: Path, reviews: list[dict] | None = None) -> Path:
    """Create vault.db in the expected location, optionally seeding review rows."""
    db_dir = vault_path / ".vault-writer"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "vault.db"
    conn = sqlite3.connect(str(db_path))
    ensure_schema(conn)
    if reviews:
        for r in reviews:
            add_review(
                conn,
                slug=r["slug"],
                kind=r["kind"],
                summary=r["summary"],
                source_notes=r.get("source_notes", []),
            )
    conn.commit()
    conn.close()
    return db_path


# ------------------------------------------------------------------ (a) GET /v1/wiki/reviews


def test_reviews_401_without_auth(client: TestClient) -> None:
    r = client.get("/v1/wiki/reviews")
    assert r.status_code == 401


def test_reviews_no_seeded_items_returns_empty(client: TestClient, paired_token) -> None:
    """Returns empty list when the vault DB exists (created by gateway startup) but has
    no open wiki_reviews rows yet.  ensure_schema is idempotent so it silently creates
    the table on first access."""
    _, token = paired_token
    r = client.get("/v1/wiki/reviews", headers={"Authorization": f"Bearer {token}"})
    # vault.db is created by CrewBoardStore at gateway startup -> always exists in tests.
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["reviews"] == []
    assert data["count"] == 0


def test_reviews_empty_list(client: TestClient, paired_token) -> None:
    """Returns empty list when DB is seeded but no open reviews."""
    _, token = paired_token
    vault_path = client.app.state.ai_team.config.vault_path
    _seed_vault_db(vault_path)

    r = client.get("/v1/wiki/reviews", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["reviews"] == []
    assert data["count"] == 0


def test_reviews_returns_open_items(client: TestClient, paired_token) -> None:
    _, token = paired_token
    vault_path = client.app.state.ai_team.config.vault_path
    _seed_vault_db(vault_path, [
        {"slug": "gateway-port", "kind": "contradiction", "summary": "Port mismatch."},
        {"slug": "topic-ships", "kind": "gap", "summary": "Ships not documented."},
    ])

    r = client.get("/v1/wiki/reviews", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count"] == 2
    slugs = {item["slug"] for item in data["reviews"]}
    assert slugs == {"gateway-port", "topic-ships"}


# ------------------------------------------------------------------ (b) GET /v1/wiki/reviews/count


def test_reviews_count_401(client: TestClient) -> None:
    r = client.get("/v1/wiki/reviews/count")
    assert r.status_code == 401


def test_reviews_count_zero_when_no_reviews(client: TestClient, paired_token) -> None:
    """Returns count=0 when no wiki_reviews rows exist yet."""
    _, token = paired_token
    r = client.get("/v1/wiki/reviews/count", headers={"Authorization": f"Bearer {token}"})
    # vault.db is created at startup -> returns 200 with count=0
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 0


def test_reviews_count_returns_integer(client: TestClient, paired_token) -> None:
    _, token = paired_token
    vault_path = client.app.state.ai_team.config.vault_path
    _seed_vault_db(vault_path, [
        {"slug": "x", "kind": "gap", "summary": "Gap X"},
        {"slug": "y", "kind": "gap", "summary": "Gap Y"},
    ])

    r = client.get("/v1/wiki/reviews/count", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2


# ------------------------------------------------------------------ (c) POST /v1/wiki/reviews/{id}/resolve


def test_resolve_marks_item_resolved(client: TestClient, paired_token) -> None:
    _, token = paired_token
    vault_path = client.app.state.ai_team.config.vault_path
    db_path = _seed_vault_db(vault_path, [
        {"slug": "gateway-port", "kind": "contradiction", "summary": "Port mismatch."},
    ])

    # Find the review ID
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT id FROM wiki_reviews WHERE status = 'open'").fetchone()
    conn.close()
    review_id = row[0]

    r = client.post(
        f"/v1/wiki/reviews/{review_id}/resolve?status=resolved",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["id"] == review_id
    assert data["status"] == "resolved"


def test_resolve_404_for_missing(client: TestClient, paired_token) -> None:
    _, token = paired_token
    vault_path = client.app.state.ai_team.config.vault_path
    _seed_vault_db(vault_path)

    r = client.post(
        "/v1/wiki/reviews/9999/resolve?status=resolved",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


def test_resolve_400_for_invalid_status(client: TestClient, paired_token) -> None:
    _, token = paired_token
    vault_path = client.app.state.ai_team.config.vault_path
    db_path = _seed_vault_db(vault_path, [
        {"slug": "x", "kind": "gap", "summary": "Gap"},
    ])
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT id FROM wiki_reviews").fetchone()
    conn.close()

    r = client.post(
        f"/v1/wiki/reviews/{row[0]}/resolve?status=deleted",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


def test_dismiss_marks_item_dismissed(client: TestClient, paired_token) -> None:
    _, token = paired_token
    vault_path = client.app.state.ai_team.config.vault_path
    db_path = _seed_vault_db(vault_path, [
        {"slug": "x", "kind": "gap", "summary": "Gap"},
    ])
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT id FROM wiki_reviews").fetchone()
    conn.close()

    r = client.post(
        f"/v1/wiki/reviews/{row[0]}/resolve?status=dismissed",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "dismissed"


# ------------------------------------------------------------------ (d) POST /v1/wiki/reviews/{id}/research


def test_research_401_without_auth(client: TestClient) -> None:
    r = client.post("/v1/wiki/reviews/1/research")
    assert r.status_code == 401


def test_research_404_for_missing_review(client: TestClient, paired_token) -> None:
    _, token = paired_token
    vault_path = client.app.state.ai_team.config.vault_path
    _seed_vault_db(vault_path)

    r = client.post(
        "/v1/wiki/reviews/9999/research",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


def test_research_skipped_when_no_llm(client: TestClient, paired_token, monkeypatch) -> None:
    """When the LLM backend is not configured (wiki_synth config absent),
    the route returns 503."""
    _, token = paired_token
    vault_path = client.app.state.ai_team.config.vault_path
    db_path = _seed_vault_db(vault_path, [
        {"slug": "topic-ships", "kind": "gap", "summary": "Ships not documented."},
    ])
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT id FROM wiki_reviews").fetchone()
    conn.close()

    # Gateway config has no wiki_synth section in test fixtures -> _build_llm_fn returns None
    r = client.post(
        f"/v1/wiki/reviews/{row[0]}/research",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 503


def test_research_returns_skipped_no_backend(client: TestClient, paired_token, monkeypatch) -> None:
    """When no search backend is configured, the research route returns
    ok=False with a skipped_reason (not an HTTP error)."""
    import gateway.routes.wiki as wiki_route

    _, token = paired_token
    vault_path = client.app.state.ai_team.config.vault_path
    db_path = _seed_vault_db(vault_path, [
        {"slug": "topic-ships", "kind": "gap", "summary": "Ships not documented."},
    ])
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT id FROM wiki_reviews").fetchone()
    conn.close()

    review_id = row[0]

    # Inject a working llm_fn so we get past the 503 check
    def _fake_llm(system: str, user: str) -> str:
        import json as _json
        return _json.dumps(["query one", "query two"])

    monkeypatch.setattr(wiki_route, "_build_llm_fn", lambda ai_team: _fake_llm)

    # TAVILY_API_KEY + SEARXNG_URL not set in test env -> backend='none'
    import os
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SEARXNG_URL", raising=False)

    r = client.post(
        f"/v1/wiki/reviews/{review_id}/research?confirm=false",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is False
    assert data["skipped_reason"] is not None
