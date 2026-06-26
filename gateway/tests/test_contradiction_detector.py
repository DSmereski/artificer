"""Tests for the embedding-based EntityContradictionDetector.

Stubs Ollama embeddings (no network), captures vault_learn calls.
"""

from __future__ import annotations

from typing import Any

import pytest

from gateway.contradiction_detector import (
    EntityContradictionDetector,
    _cosine,
    _has_negation_cue,
)


class _FakeVaultClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def learn(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True}


def _detector_with(
    fake_client: _FakeVaultClient,
    prior_vec: list[float] | None,
    new_vec: list[float] | None,
) -> EntityContradictionDetector:
    det = EntityContradictionDetector(
        vault_client_factory=lambda: fake_client,
    )
    seq = iter([prior_vec, new_vec])

    async def _embed(_text: str):
        return next(seq)

    det._embed = _embed  # type: ignore[assignment]
    return det


def test_cosine_basic():
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert _cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
    assert _cosine([], [1.0]) == 0.0
    assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_negation_cue_matches():
    assert _has_negation_cue("Actually that's wrong, the ship is red")
    assert _has_negation_cue("She is no longer working there")
    assert not _has_negation_cue("She works at the gallery")


@pytest.mark.asyncio
async def test_skips_when_no_negation_cue():
    fake = _FakeVaultClient()
    det = _detector_with(fake, [1.0, 0.0], [0.0, 1.0])
    flagged = await det.check(
        slug="kraken", title="Kraken",
        prior="A capital ship from Star Citizen.",
        new="A capital ship from Star Citizen with a hangar bay.",
        bot="hive",
    )
    assert flagged is False
    assert fake.calls == []


@pytest.mark.asyncio
async def test_skips_when_similarity_above_threshold():
    fake = _FakeVaultClient()
    # Very similar vectors → cosine ~1
    det = _detector_with(fake, [1.0, 0.0, 0.1], [1.0, 0.0, 0.05])
    flagged = await det.check(
        slug="penguin", title="Penguin",
        prior="Penguin's favorite color is red.",
        new="Actually Penguin's favorite color is still red.",
        bot="hive",
    )
    assert flagged is False
    assert fake.calls == []


@pytest.mark.asyncio
async def test_flags_when_low_similarity_and_negation():
    fake = _FakeVaultClient()
    # Orthogonal vectors → cosine 0 (well below 0.6)
    det = _detector_with(fake, [1.0, 0.0], [0.0, 1.0])
    flagged = await det.check(
        slug="penguin", title="Penguin",
        prior="Penguin's favorite color is red.",
        new="Actually Penguin's favorite color is teal, not red.",
        bot="hive",
        device_audience=["hive"],
    )
    assert flagged is True
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["category"] == "knowledge"
    assert "Contradiction" in call["title"]
    assert "Penguin" in call["title"]
    assert "contradiction" in call["tags"]
    assert "entity" in call["tags"]
    assert "red" in call["body"]
    assert "teal" in call["body"]


@pytest.mark.asyncio
async def test_skips_when_embed_returns_none():
    fake = _FakeVaultClient()
    det = _detector_with(fake, None, None)
    flagged = await det.check(
        slug="x", title="X",
        prior="Was happy.", new="Actually was not happy.",
    )
    assert flagged is False
    assert fake.calls == []


@pytest.mark.asyncio
async def test_skips_when_prior_or_new_empty():
    fake = _FakeVaultClient()
    det = _detector_with(fake, [1.0], [0.0])
    assert await det.check(slug="x", title="X", prior="", new="not happy") is False
    assert await det.check(slug="x", title="X", prior="happy", new="") is False
    assert await det.check(slug="x", title="X", prior="same", new="same") is False
    assert fake.calls == []
