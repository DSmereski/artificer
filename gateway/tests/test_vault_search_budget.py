"""Tests for the token-budget parameter on GET /v1/vault/search.

The budget=N query param allocates at most N tokens (chars/4 estimate) across
the ranked hits. The top hit is always kept whole; lower hits are truncated or
dropped.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from gateway.routes import vault as vault_route
from gateway.routes.vault import _apply_token_budget, _estimate_tokens, SearchHit


# ---------------------------------------------------------------------------
# Unit tests: pure budget logic (no HTTP)
# ---------------------------------------------------------------------------


def _make_hit(path: str, preview: str, score: float = 0.9) -> SearchHit:
    return SearchHit(
        path=path,
        type="knowledge",
        author="test",
        audience=["all"],
        score=score,
        preview=preview,
    )


def test_estimate_tokens_basic() -> None:
    assert _estimate_tokens("abcd") == 1        # 4 chars → 1 token
    assert _estimate_tokens("a" * 400) == 100   # 400 chars → 100 tokens
    assert _estimate_tokens("") == 1            # floor at 1


def test_apply_budget_empty_hits() -> None:
    assert _apply_token_budget([], [], budget=100) == []


def test_apply_budget_zero_budget_passthrough() -> None:
    """budget=0 means unlimited — handled upstream; function is not called."""
    hits = [_make_hit("a.md", "x" * 100)]
    bodies = ["x" * 100]
    # budget>0 guard is enforced by the route; test _apply_token_budget directly
    # with a very large budget to confirm all hits pass through unmodified.
    result = _apply_token_budget(hits, bodies, budget=999999)
    assert len(result) == 1
    assert result[0].preview == "x" * 100


def test_apply_budget_top_hit_always_kept_whole() -> None:
    """Even when the top hit alone exceeds budget, it is kept untruncated."""
    big_body = "w" * 2000   # ~500 tokens
    hits = [_make_hit("top.md", big_body.replace("\n", " "))]
    bodies = [big_body]
    result = _apply_token_budget(hits, bodies, budget=10)
    assert len(result) == 1
    assert result[0].path == "top.md"
    # Preview should be unchanged (the top hit is kept whole).
    assert result[0].preview == big_body


def test_apply_budget_drops_tail_over_budget() -> None:
    """Hits whose bodies won't fit in the remaining budget are dropped."""
    body_a = "a" * 40    # 10 tokens
    body_b = "b" * 40    # 10 tokens
    body_c = "c" * 40    # 10 tokens
    hits = [
        _make_hit("a.md", body_a, score=0.9),
        _make_hit("b.md", body_b, score=0.8),
        _make_hit("c.md", body_c, score=0.7),
    ]
    bodies = [body_a, body_b, body_c]
    # Budget = 15 tokens. Top hit costs 10 → 5 remain. b costs 10 → truncated.
    # c gets 0 remaining after b is truncated → dropped.
    result = _apply_token_budget(hits, bodies, budget=15)
    assert any(h.path == "a.md" for h in result), "top hit must be present"
    # a.md is always kept whole
    a_hit = next(h for h in result if h.path == "a.md")
    assert a_hit.preview == body_a
    # c.md should be absent (budget exhausted after b)
    assert not any(h.path == "c.md" for h in result)


def test_apply_budget_total_tokens_within_budget() -> None:
    """The total estimated tokens of returned hits must not exceed budget."""
    bodies = ["x" * 80, "y" * 200, "z" * 400]   # 20, 50, 100 tokens
    hits = [
        _make_hit(f"note{i}.md", b.replace("\n", " "), score=0.9 - i * 0.1)
        for i, b in enumerate(bodies)
    ]
    budget = 60   # Should accommodate hit 0 (20) + hit 1 (50) exactly, then drop hit 2

    result = _apply_token_budget(hits, bodies, budget=budget)

    # Measure tokens of returned previews.
    total_toks = sum(_estimate_tokens(h.preview) for h in result)
    assert total_toks <= budget, f"total tokens {total_toks} exceeded budget {budget}"
    assert result[0].path == "note0.md", "top hit must be first"


def test_apply_budget_truncates_partial_hit() -> None:
    """A hit that partially fits has its preview truncated to the allowed chars."""
    top_body = "a" * 40    # 10 tokens
    partial_body = "b" * 200  # 50 tokens, but only 20 tokens of budget left
    hits = [
        _make_hit("top.md", top_body, score=0.9),
        _make_hit("partial.md", partial_body, score=0.8),
    ]
    bodies = [top_body, partial_body]
    budget = 30  # 10 used by top, 20 left for partial
    result = _apply_token_budget(hits, bodies, budget=budget)

    partial_hit = next((h for h in result if h.path == "partial.md"), None)
    assert partial_hit is not None, "partial hit should be included (truncated)"
    # Truncated body has at most budget - top_tokens = 20 tokens → 80 chars + "..."
    assert partial_hit.preview.endswith("...")
    char_limit = 20 * 4  # 20 tokens × 4 chars/token
    # Preview should be chars_allowed + "..." ≤ char_limit + 3
    assert len(partial_hit.preview) <= char_limit + 3


# ---------------------------------------------------------------------------
# Integration test: budget param via HTTP
# ---------------------------------------------------------------------------


def _fake_search_result(path: str, body: str):
    class _R:
        def __init__(self) -> None:
            self.path = path
            self.note_type = "knowledge"
            self.author = "test"
            self.audience = ["all"]
            self.body = body
            self.frontmatter = {}
            self.score = 0.9

    return _R()


class _FakeClientBudget:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def search(self, *, query_embedding, k, audience, query_text=None):
        # Return 3 hits with progressively larger bodies.
        return [
            _fake_search_result("a/top.md", "a" * 40),     # 10 tokens
            _fake_search_result("b/mid.md", "b" * 200),    # 50 tokens
            _fake_search_result("c/tail.md", "c" * 400),   # 100 tokens
        ]

    async def learn(self, **kwargs):
        return {"ok": True, "path": "x.md", "created": True}


async def _fake_embed(ollama_url: str, model: str, text: str) -> list[float]:
    return [0.1] * 8


def test_vault_search_budget_via_http(
    client: TestClient, paired_token: tuple[str, str], monkeypatch
) -> None:
    """budget=20 → top hit (10 tok) kept whole + mid partially included; tail dropped."""
    monkeypatch.setattr(vault_route, "_embed_query", _fake_embed)
    monkeypatch.setattr("shared.vault_client.VaultClient", _FakeClientBudget)
    _, token = paired_token

    r = client.get(
        "/v1/vault/search?q=test&budget=20",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    hits = r.json()
    paths = [h["path"] for h in hits]

    # Top hit must always be present.
    assert "a/top.md" in paths

    # Tail hit should be absent (budget exhausted).
    assert "c/tail.md" not in paths

    # Total estimated tokens should be ≤ budget.
    total = sum(_estimate_tokens(h["preview"]) for h in hits)
    assert total <= 20, f"total tokens {total} exceeded budget 20"


def test_vault_search_no_budget_returns_all(
    client: TestClient, paired_token: tuple[str, str], monkeypatch
) -> None:
    """With no budget param (default 0 = unlimited), all hits are returned."""
    monkeypatch.setattr(vault_route, "_embed_query", _fake_embed)
    monkeypatch.setattr("shared.vault_client.VaultClient", _FakeClientBudget)
    _, token = paired_token

    r = client.get(
        "/v1/vault/search?q=test",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    hits = r.json()
    # All 3 hits from the fake client should be present.
    paths = [h["path"] for h in hits]
    assert "a/top.md" in paths
    assert "b/mid.md" in paths
    assert "c/tail.md" in paths


def test_vault_search_budget_top_hit_present_even_when_over(
    client: TestClient, paired_token: tuple[str, str], monkeypatch
) -> None:
    """Top hit is kept even when its body alone exceeds the budget."""

    class _FatTop:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def search(self, *, query_embedding, k, audience, query_text=None):
            return [
                _fake_search_result("big/top.md", "z" * 2000),   # 500 tokens
                _fake_search_result("small/other.md", "s" * 40),  # 10 tokens
            ]

        async def learn(self, **kwargs):
            return {"ok": True, "path": "x.md", "created": True}

    monkeypatch.setattr(vault_route, "_embed_query", _fake_embed)
    monkeypatch.setattr("shared.vault_client.VaultClient", _FatTop)
    _, token = paired_token

    r = client.get(
        "/v1/vault/search?q=test&budget=5",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    hits = r.json()
    paths = [h["path"] for h in hits]
    assert "big/top.md" in paths, "top hit must always be returned"
    # Other hit should be dropped (budget exceeded by top hit alone).
    assert "small/other.md" not in paths
