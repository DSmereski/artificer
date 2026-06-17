"""Unit tests for gateway.image_lora_doc."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gateway.image_lora_doc import regenerate_if_stale, render_catalog


_SAMPLE_REGISTRY = [
    {"alias": "Real Beauty", "pipeline": "sdxl", "trigger_words": "",
     "default_strength": 0.8, "category": "people", "nsfw": False},
    {"alias": "Naughty Thing", "pipeline": "sdxl", "trigger_words": "spicy",
     "default_strength": 0.9, "category": "nsfw", "nsfw": True},
    {"alias": "Cyberpunk Style", "pipeline": "flux", "trigger_words": "neon city",
     "default_strength": 1.0, "category": "style", "nsfw": False},
    {"alias": "Old Lora", "pipeline": "sd15", "trigger_words": "",
     "default_strength": 1.0, "category": "", "nsfw": False},
]


# ---------------------------------------------------------------- render


def test_render_includes_count():
    md = render_catalog(_SAMPLE_REGISTRY)
    assert "**4 total**" in md
    assert "1 NSFW" in md


def test_render_groups_by_pipeline():
    md = render_catalog(_SAMPLE_REGISTRY)
    assert "## FLUX (1)" in md
    assert "## SDXL / Pony (2)" in md
    assert "## SD 1.5 (1)" in md


def test_render_marks_nsfw():
    md = render_catalog(_SAMPLE_REGISTRY)
    # NSFW row should carry the 🔞 marker
    assert "Naughty Thing 🔞" in md


def test_render_skips_entries_without_alias():
    bad = list(_SAMPLE_REGISTRY) + [{"alias": "", "pipeline": "flux"}]
    md = render_catalog(bad)
    # Still 4 valid LoRAs
    assert "**4 total**" in md


# ---------------------------------------------------------------- regenerate_if_stale


def test_regenerate_creates_when_canon_missing(tmp_path: Path):
    registry = tmp_path / "registry.json"
    canon = tmp_path / "canon.md"
    registry.write_text(json.dumps(_SAMPLE_REGISTRY), encoding="utf-8")
    rewrote, n = regenerate_if_stale(registry_path=registry, canon_path=canon)
    assert rewrote is True
    assert n == 4
    assert canon.exists()
    body = canon.read_text(encoding="utf-8")
    assert "Real Beauty" in body
    assert "Cyberpunk Style" in body


def test_regenerate_skips_when_canon_fresh(tmp_path: Path):
    registry = tmp_path / "registry.json"
    canon = tmp_path / "canon.md"
    registry.write_text(json.dumps(_SAMPLE_REGISTRY), encoding="utf-8")
    canon.write_text("stale", encoding="utf-8")
    # Make canon newer than registry by 5 seconds.
    new_mtime = time.time()
    import os
    os.utime(registry, (new_mtime - 10, new_mtime - 10))
    os.utime(canon, (new_mtime, new_mtime))
    rewrote, _ = regenerate_if_stale(registry_path=registry, canon_path=canon)
    assert rewrote is False
    # Body untouched
    assert canon.read_text(encoding="utf-8") == "stale"


def test_regenerate_rewrites_when_registry_newer(tmp_path: Path):
    registry = tmp_path / "registry.json"
    canon = tmp_path / "canon.md"
    registry.write_text(json.dumps(_SAMPLE_REGISTRY), encoding="utf-8")
    canon.write_text("stale", encoding="utf-8")
    import os
    now = time.time()
    os.utime(canon, (now - 100, now - 100))
    os.utime(registry, (now, now))
    rewrote, n = regenerate_if_stale(registry_path=registry, canon_path=canon)
    assert rewrote is True
    assert n == 4
    body = canon.read_text(encoding="utf-8")
    assert "stale" not in body
    assert "Real Beauty" in body


def test_regenerate_handles_missing_registry(tmp_path: Path):
    registry = tmp_path / "missing.json"
    canon = tmp_path / "canon.md"
    rewrote, n = regenerate_if_stale(registry_path=registry, canon_path=canon)
    assert rewrote is False
    assert n == 0
    assert not canon.exists()


def test_regenerate_handles_garbage_registry(tmp_path: Path):
    registry = tmp_path / "registry.json"
    canon = tmp_path / "canon.md"
    registry.write_text("not json", encoding="utf-8")
    rewrote, n = regenerate_if_stale(registry_path=registry, canon_path=canon)
    assert rewrote is False
    assert n == 0
