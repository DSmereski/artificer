"""Tests for the Corsair iCUE theme-RGB helper (#189).

The SDK itself needs a running iCUE + hardware, so these cover the pure logic:
the per-theme colour map and the fail-safe no-op behaviour. The actual paint is
exercised live (see the device smoke test in the task notes).
"""
import sys
import types

import gateway.helpers.icue as icue
from gateway.routes.theme import _THEMES


def test_icue_rgb_covers_every_theme():
    # Every shipped UI theme must have an iCUE accent, else picking it leaves
    # the keyboard on the previous colour.
    assert set(icue._ICUE_RGB) == set(_THEMES)


def test_icue_rgb_values_are_valid_bytes():
    for name, rgb in icue._ICUE_RGB.items():
        assert len(rgb) == 3, name
        assert all(0 <= c <= 255 for c in rgb), name


def test_set_theme_unknown_is_noop():
    assert icue.set_theme("does-not-exist") is False


def test_set_color_no_sdk_is_graceful(monkeypatch):
    # Simulate the cuesdk package being absent: set_color must return False, not
    # raise, so a theme PUT never fails because lighting is unavailable.
    monkeypatch.setitem(sys.modules, "cuesdk", None)  # import → TypeError, caught
    # Force a fresh connect attempt.
    monkeypatch.setattr(icue, "_connected", False)
    monkeypatch.setattr(icue, "_sdk", None)
    assert icue.set_color(10, 20, 30) is False


def test_set_theme_maps_to_set_color(monkeypatch):
    seen = {}
    monkeypatch.setattr(icue, "set_color", lambda r, g, b: seen.update(rgb=(r, g, b)) or True)
    assert icue.set_theme("nod") is True
    assert seen["rgb"] == icue._ICUE_RGB["nod"]
