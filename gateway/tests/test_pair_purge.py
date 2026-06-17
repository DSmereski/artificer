"""Tests for _is_transient_device_name — H-1 security fix.

Verifies that the tightened prefix matching does not nuke real devices
whose names happen to share a short prefix with smoke/test names.
"""

from __future__ import annotations

import pytest

from gateway.routes.pair import _is_transient_device_name


# ---------------------------------------------------------------- must be purged

@pytest.mark.parametrize("name", [
    # Exact-match prefixes (legacy short names)
    "smoke",
    "vault-smoke",
    "video-smoke",
    "hive",
    "log",
    "final",
    "verify",
    "apk",
    "skills",
    "multi",
    "pytest-device",
    "smoke-test",
    # Stamp-suffix style (hex stamp ≥ 8 chars)
    "smoke-deadbeef1234",
    "smoke-a1b2c3d4e5f6",
    "hive-deadbeef12345678",
    "log-cafebabe1234",
    "verify-00112233445566",
    "final-abcdef0123456789",
    # Stamp with numeric tail
    "smoke-deadbeef1234-2",
    "hive-a1b2c3d4e5f6-1",
])
def test_is_transient_true(name: str) -> None:
    assert _is_transient_device_name(name) is True, (
        f"Expected {name!r} to be treated as transient"
    )


# ---------------------------------------------------------------- must NOT be purged

@pytest.mark.parametrize("name", [
    # Real user devices that share a short prefix with smoke names
    "hive-android-phone",
    "hive-phone",
    "log-server",
    "final-pc",
    "verify-laptop",
    "apk-builder",
    "skills-tracker",
    "multi-monitor-pc",
    # Completely unrelated names
    "android-phone",
    "windows-pc",
    "real-phone",
    "ipad-pro",
    "my-tablet",
    # Stamp that is too short (< 8 hex chars) — not a valid hex stamp
    "smoke-abc123",          # only 6 hex chars
    "hive-deadbee",          # only 7 hex chars
    # Mixed-case or non-hex suffix after the prefix
    "smoke-MyPhone",
    "hive-android-12",
    "log-prod-server",
])
def test_is_transient_false(name: str) -> None:
    assert _is_transient_device_name(name) is False, (
        f"Expected {name!r} to be treated as a real (non-transient) device"
    )
