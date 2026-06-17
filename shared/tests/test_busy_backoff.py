"""Backoff sequence + bounds for the SQLite-busy retry helper.

Patches time.sleep so we can observe the delay sequence without
actually waiting. Confirms exponential growth, the cap, and that the
jittered value stays inside the documented [0.5x, 1.5x] window.
"""
from __future__ import annotations

import pytest

from shared import vault_client


def test_backoff_doubles_within_jitter_window(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(vault_client.time, "sleep", sleeps.append)
    # Force jitter factor to its midpoint (1.0).
    monkeypatch.setattr(vault_client.random, "random", lambda: 0.5)

    for attempt in range(4):
        vault_client._busy_backoff_sleep(attempt)

    # Pre-cap base sequence: 0.05, 0.10, 0.20, 0.40 (cap hit).
    assert sleeps == pytest.approx([0.05, 0.10, 0.20, 0.40])


def test_backoff_is_capped(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(vault_client.time, "sleep", sleeps.append)
    monkeypatch.setattr(vault_client.random, "random", lambda: 0.5)

    # Large attempt index should still respect _BUSY_BACKOFF_CAP_S.
    vault_client._busy_backoff_sleep(20)
    assert sleeps[-1] <= vault_client._BUSY_BACKOFF_CAP_S * 1.5 + 1e-9


def test_jitter_keeps_window(monkeypatch):
    # With random() = 0 → factor 0.5; with random() = 1 → factor 1.5.
    sleeps: list[float] = []
    monkeypatch.setattr(vault_client.time, "sleep", sleeps.append)

    monkeypatch.setattr(vault_client.random, "random", lambda: 0.0)
    vault_client._busy_backoff_sleep(0)
    monkeypatch.setattr(vault_client.random, "random", lambda: 1.0)
    vault_client._busy_backoff_sleep(0)

    low, high = sleeps
    assert low == pytest.approx(0.025)   # 0.05 * 0.5
    assert high == pytest.approx(0.075)  # 0.05 * 1.5
