"""Tests for the M5.1 image build slot machine."""

from __future__ import annotations

import time

import pytest

from gateway.image_build_state import ImageBuildState, ImageBuildStore


def test_default_state_not_ready():
    s = ImageBuildState(device_id="d1")
    assert s.is_ready() is False
    assert "<unset>" in s.render_block()
    assert "Missing required slots" in s.render_block()


def test_ready_when_required_slots_filled():
    s = ImageBuildState(device_id="d1", subject="elf", aspect="portrait")
    assert s.is_ready() is True
    assert "[CONFIRM_IMAGE]" in s.render_block()


def test_apply_updates_changes_slots():
    s = ImageBuildState(device_id="d1")
    changed = s.apply_updates({"subject": "elf", "aspect": "portrait"})
    assert "subject" in changed and "aspect" in changed
    assert s.subject == "elf"
    assert s.aspect == "portrait"


def test_apply_updates_ignores_unknown_keys():
    s = ImageBuildState(device_id="d1")
    changed = s.apply_updates({"subject": "elf", "evil_key": "boom"})
    assert changed == ["subject"]


def test_apply_updates_lora_list():
    s = ImageBuildState(device_id="d1")
    s.apply_updates({"style_loras": ["A", "B"]})
    assert s.style_loras == ["A", "B"]


def test_apply_updates_count_int():
    s = ImageBuildState(device_id="d1")
    s.apply_updates({"count": 4})
    assert s.count == 4
    s.apply_updates({"count": -1})         # invalid — ignored
    assert s.count == 4


def test_apply_updates_no_change_returns_empty():
    s = ImageBuildState(device_id="d1", subject="elf")
    changed = s.apply_updates({"subject": "elf"})
    assert changed == []


def test_is_stale():
    s = ImageBuildState(device_id="d1")
    s.last_touched = time.time() - 31 * 60
    assert s.is_stale() is True


# ---------------------------------------------------------------- store


def test_store_get_or_create(tmp_path):
    store = ImageBuildStore(tmp_path)
    s = store.get_or_create("dev1")
    assert s.device_id == "dev1"
    assert (tmp_path / "dev1.json").is_file()


def test_store_persists_and_reloads(tmp_path):
    store = ImageBuildStore(tmp_path)
    store.update("dev1", {"subject": "elf", "aspect": "portrait"})
    # Simulate gateway restart.
    store2 = ImageBuildStore(tmp_path)
    s = store2.get("dev1")
    assert s is not None
    assert s.subject == "elf"
    assert s.aspect == "portrait"
    assert s.is_ready() is True


def test_store_clear(tmp_path):
    store = ImageBuildStore(tmp_path)
    store.update("dev1", {"subject": "x"})
    assert store.get("dev1") is not None
    store.clear("dev1")
    assert store.get("dev1") is None
    assert not (tmp_path / "dev1.json").is_file()


def test_store_drops_stale_on_load(tmp_path):
    store = ImageBuildStore(tmp_path)
    store.update("dev1", {"subject": "x"})
    # Tamper: rewrite the on-disk last_touched far in the past.
    import json
    p = tmp_path / "dev1.json"
    obj = json.loads(p.read_text())
    obj["last_touched"] = time.time() - 60 * 60
    p.write_text(json.dumps(obj))
    # Reload: stale entry gone.
    store2 = ImageBuildStore(tmp_path)
    assert store2.get("dev1") is None


def test_store_safe_filename_chars(tmp_path):
    store = ImageBuildStore(tmp_path)
    store.update("dev/with/path", {"subject": "x"})
    # Must NOT have created a directory traversal file.
    bad = tmp_path / "dev" / "with"
    assert not bad.exists()


def test_store_cleanup_stale(tmp_path):
    store = ImageBuildStore(tmp_path)
    store.update("a", {"subject": "x"})
    store.update("b", {"subject": "y"})
    # Make 'a' stale.
    store._cache["a"].last_touched = time.time() - 60 * 60
    dropped = store.cleanup_stale()
    assert dropped == 1
    assert store.get("a") is None
    assert store.get("b") is not None
