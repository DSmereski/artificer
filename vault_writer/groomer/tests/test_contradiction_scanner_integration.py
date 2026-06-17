# vault_writer/groomer/tests/test_contradiction_scanner_integration.py
"""Integration test: contradiction_scanner reads entity_pages from a real
VaultIndex, embeds compiled_truth + recent timeline entry on-the-fly via
ctx.embedder, and emits a suggestion when cosine drops below threshold.

Without this wiring, contradiction_scanner returns [] in production
because there's no list_entity_pages_with_embeddings (or equivalent)
method on VaultIndex — closing the second half of #433."""
from __future__ import annotations

from pathlib import Path

import pytest


class _FakeEmbedder:
    """Returns a different unit vector for each unique input string.
    Two inputs that share a common prefix get vectors close together;
    truly divergent inputs get orthogonal vectors so cosine ≈ 0."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        # Tag the vector by the FIRST WORD of the text so the test can
        # control divergence: "alpha ..." -> [1,0,0]; "beta ..." -> [0,1,0].
        first = (text.strip().split() or [""])[0].lower()
        if first.startswith("alpha"):
            return [1.0, 0.0, 0.0]
        if first.startswith("beta"):
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


@pytest.mark.asyncio
async def test_contradiction_scanner_flags_divergent_entity(
    tmp_path: Path,
) -> None:
    from vault_writer.groomer.groom_run import run_groom
    from vault_writer.index import VaultIndex

    db_dir = tmp_path / ".vault-writer"
    db_dir.mkdir()
    db_path = db_dir / "vault.db"
    idx = VaultIndex.open(db_path, dimension=3)
    try:
        # Seed an entity whose compiled_truth and most-recent timeline
        # entry start with different keywords, so the fake embedder
        # returns orthogonal vectors → cosine 0 → contradiction flagged.
        idx.entity_page_upsert(
            slug="kraken",
            kind="thing",
            title="Kraken",
            compiled_truth="alpha kraken is friendly to sailors",
            timeline_entry="alpha first noted as friendly",
            now_epoch=1000,
        )
        idx.entity_page_upsert(
            slug="kraken",
            kind="thing",
            title="Kraken",
            compiled_truth="alpha kraken is friendly to sailors",
            timeline_entry="beta actually it eats ships",
            now_epoch=2000,
        )
    finally:
        idx.close()

    # Need a markdown file too — other scanners walk the vault.
    (tmp_path / "things").mkdir()
    (tmp_path / "things" / "kraken.md").write_text(
        "# Kraken\n", encoding="utf-8",
    )

    embedder = _FakeEmbedder()
    counts = await run_groom(vault_path=tmp_path, embedder=embedder)

    assert counts["contradiction_scanner"] >= 1, (
        f"contradiction_scanner should flag divergent entity; counts={counts}"
    )
    out_dir = tmp_path / "ops" / "groomer" / "contradiction_scanner"
    assert out_dir.exists()
    files = list(out_dir.iterdir())
    assert any("kraken" in f.name for f in files), (
        f"expected a kraken proposal; got {[f.name for f in files]}"
    )
    # Confirm the embedder was actually invoked — guards against a
    # silent regression where the scanner stays inert.
    assert embedder.calls, "embedder.embed() was never called"


@pytest.mark.asyncio
async def test_contradiction_scanner_inert_without_embedder(
    tmp_path: Path,
) -> None:
    """If no embedder is supplied (and run_groom can't auto-open one),
    the scanner gracefully returns [] rather than raising."""
    from vault_writer.groomer.groom_run import run_groom
    from vault_writer.index import VaultIndex

    db_dir = tmp_path / ".vault-writer"
    db_dir.mkdir()
    db_path = db_dir / "vault.db"
    idx = VaultIndex.open(db_path, dimension=3)
    try:
        idx.entity_page_upsert(
            slug="kraken", kind="thing", title="Kraken",
            compiled_truth="anything",
            timeline_entry="anything",
            now_epoch=1000,
        )
    finally:
        idx.close()

    counts = await run_groom(vault_path=tmp_path)  # no embedder
    assert counts["contradiction_scanner"] == 0
