"""Tests for vault_writer.smart_link — the periodic graph linker.

Locks the two properties that matter operationally:
  * every target note ends up linked (no loose graph dots), and
  * a second pass is a no-op (idempotent — no perpetual git churn).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vault_writer.smart_link import START, run_link


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    # real notes (get hub + similarity related)
    _write(v / "knowledge" / "imagegen-loras.md",
           "---\ntitle: imagegen loras\ntags:\n- lora\n---\n# imagegen loras\n")
    _write(v / "knowledge" / "lora-index.md",
           "---\ntitle: lora index\ntags:\n- lora\n---\n# lora index\n")
    _write(v / "loras" / "style-a.md",
           "---\ntitle: style-a lora\ntags:\n- lora\n---\n# style-a\n")
    _write(v / "loras" / "INDEX.md", "# Loras index\n")          # reused hub
    _write(v / "wiki" / "index.md", "# Wiki index\n")            # reused hub
    _write(v / "tools" / "comfyui.md",
           "---\ntitle: ComfyUI\ntags:\n- comfyui\n---\n# ComfyUI\n")
    # aux notes (hub-only)
    _write(v / "tasks" / "T-0001.md", "# Task 1\nbody\n")
    # dup_scanner pair (links to both sides if they exist as notes)
    _write(v / "ops" / "groomer" / "dup_scanner" / "imagegen-loras__lora-index.md",
           "# dup report\n")
    return v


def _orphans(vault: Path, exclude=("README.md",)) -> list[str]:
    out = []
    for p in vault.rglob("*.md"):
        if p.name in exclude:
            continue
        if "[[" not in p.read_text(encoding="utf-8"):
            out.append(p.relative_to(vault).as_posix())
    return out


def test_apply_links_everything_and_builds_hubs(vault: Path):
    stats = run_link(vault, apply=True, quiet=True)
    assert stats["notes_to_edit"] > 0
    # root INDEX + folder hubs exist
    assert (vault / "INDEX.md").exists()
    assert (vault / "knowledge" / "_MOC-knowledge.md").exists()
    assert (vault / "tasks" / "_MOC-tasks.md").exists()
    # no loose notes left (hubs/INDEX themselves are linked too)
    assert _orphans(vault) == []


def test_second_pass_is_noop(vault: Path):
    run_link(vault, apply=True, quiet=True)
    again = run_link(vault, apply=True, quiet=True)
    assert again["notes_to_edit"] == 0, "linker must converge (no git churn)"


def test_reused_hub_not_double_linked(vault: Path):
    """loras/INDEX.md is a hub, not a spoke — must not get a Related block
    that fights the hub block (the oscillation bug)."""
    run_link(vault, apply=True, quiet=True)
    idx = (vault / "loras" / "INDEX.md").read_text(encoding="utf-8")
    assert idx.count(START) == 1
    assert "map of content" in idx          # it's the hub
    assert "## Related" not in idx          # not also a spoke


def test_dup_scanner_pair_links_both_sides(vault: Path):
    run_link(vault, apply=True, quiet=True)
    dup = (vault / "ops" / "groomer" / "dup_scanner"
           / "imagegen-loras__lora-index.md").read_text(encoding="utf-8")
    assert "[[imagegen-loras]]" in dup
    assert "[[lora-index]]" in dup


def test_unlink_reverts(vault: Path):
    run_link(vault, apply=True, quiet=True)
    run_link(vault, unlink=True, quiet=True)
    # managed blocks stripped from notes
    note = (vault / "knowledge" / "imagegen-loras.md").read_text(encoding="utf-8")
    assert START not in note
    # generated hubs + INDEX deleted
    assert not (vault / "INDEX.md").exists()
    assert not (vault / "knowledge" / "_MOC-knowledge.md").exists()
