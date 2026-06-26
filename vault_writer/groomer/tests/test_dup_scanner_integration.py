# vault_writer/groomer/tests/test_dup_scanner_integration.py
"""Integration test: run_groom auto-opens VaultIndex so dup_scanner fires
end-to-end against a real sqlite-vec index.

Without the auto-open, the scanner silently returns [] in prod because
ScanContext.vault_index is None — the symptom that originally surfaced
as the CRITICAL deferred from #428."""
from __future__ import annotations

from pathlib import Path

import pytest

from vault_writer.groomer.groom_run import run_groom


@pytest.mark.asyncio
async def test_dup_scanner_wired_to_real_vault_index(tmp_path: Path) -> None:
    # Stand up a real VaultIndex with two near-duplicate notes.
    from vault_writer.index import VaultIndex

    db_dir = tmp_path / ".vault-writer"
    db_dir.mkdir()
    db_path = db_dir / "vault.db"
    idx = VaultIndex.open(db_path, dimension=3)
    try:
        idx.upsert(
            path="people/penguin.md",
            note_type="knowledge",
            author="hive",
            audience=["all"],
            frontmatter={},
            body="penguin body",
            embedding=[1.0, 0.0, 0.0],
        )
        idx.upsert(
            path="people/penguin-old.md",
            note_type="knowledge",
            author="hive",
            audience=["all"],
            frontmatter={},
            body="penguin body older copy",
            embedding=[0.99, 0.05, 0.0],
        )
    finally:
        idx.close()

    # Materialise the markdown files too — link_scanner/format_scanner/
    # stale_scanner walk the filesystem and would otherwise see an
    # empty vault.
    (tmp_path / "people").mkdir()
    (tmp_path / "people" / "penguin.md").write_text(
        "# Penguin\nfoo\n", encoding="utf-8",
    )
    (tmp_path / "people" / "penguin-old.md").write_text(
        "# Penguin (old)\nfoo\n", encoding="utf-8",
    )

    # Run groom WITHOUT passing vault_index — production path.
    counts = await run_groom(vault_path=tmp_path)

    assert counts["dup_scanner"] >= 1, (
        f"dup_scanner should fire on near-duplicate notes; counts={counts}"
    )
    dup_dir = tmp_path / "ops" / "groomer" / "dup_scanner"
    assert dup_dir.exists()
    files = list(dup_dir.iterdir())
    assert any("penguin" in f.name for f in files)
