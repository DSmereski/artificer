# vault_writer/groomer/tests/test_e2e.py
"""End-to-end groomer pass: real scanners + suggestions writer."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from vault_writer.groomer.groom_run import run_groom


@pytest.mark.asyncio
async def test_e2e_link_and_stale(tmp_path: Path) -> None:
    # 1. A note with a broken wikilink.
    (tmp_path / "people").mkdir()
    (tmp_path / "people" / "alice.md").write_text(
        "# Alice\n\nSee [[Missing Friend]] for context.\n",
        encoding="utf-8",
    )
    # 2. A stale note (mtime 7 months old).
    old = tmp_path / "old.md"
    old.write_text("# Old\nstale content\n", encoding="utf-8")
    seven_months_ago = time.time() - (60 * 60 * 24 * 30 * 7)
    os.utime(old, (seven_months_ago, seven_months_ago))

    counts = await run_groom(vault_path=tmp_path)

    # link_scanner caught the broken wikilink.
    link_dir = tmp_path / "ops" / "groomer" / "link_scanner"
    assert link_dir.exists()
    files = list(link_dir.iterdir())
    assert len(files) >= 1
    assert any("Missing Friend" in f.read_text(encoding="utf-8") for f in files)

    # stale_scanner caught the old note.
    stale_dir = tmp_path / "ops" / "groomer" / "stale_scanner"
    assert stale_dir.exists()
    assert any(f.name.startswith("old") for f in stale_dir.iterdir())

    # Run summary is materialised.
    runs_dir = tmp_path / "ops" / "groomer" / "_runs"
    assert runs_dir.exists()
    assert list(runs_dir.iterdir())

    # Counts reflect both scanners fired.
    assert counts["link_scanner"] >= 1
    assert counts["stale_scanner"] >= 1
