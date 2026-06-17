"""Tests for TurnBudget VRAM-aware adaptive parallelism."""
from __future__ import annotations

from gateway.hive_coordinator import TurnBudget


def test_turn_budget_falls_back_to_serial_under_vram_pressure():
    """When vram_provider returns 0 free, live_max_concurrent should drop to 1."""
    budget = TurnBudget(
        max_concurrent_helpers=5,
        vram_provider=lambda: 0,
        helper_vram_estimate_mb=4000,
    )
    assert budget.live_max_concurrent() == 1


def test_turn_budget_uses_vram_to_cap_concurrency():
    budget = TurnBudget(
        max_concurrent_helpers=10,
        vram_provider=lambda: 8000,
        helper_vram_estimate_mb=4000,
    )
    # 8000 mb / 4000 mb-per-helper = 2 → cap to 2 (not 10)
    assert budget.live_max_concurrent() == 2


def test_turn_budget_no_provider_returns_configured_cap():
    budget = TurnBudget(max_concurrent_helpers=5, vram_provider=None)
    assert budget.live_max_concurrent() == 5


def test_turn_budget_provider_above_max_does_not_exceed_max():
    """When VRAM is plentiful, configured max still rules."""
    budget = TurnBudget(
        max_concurrent_helpers=3,
        vram_provider=lambda: 100_000,
        helper_vram_estimate_mb=4000,
    )
    # 100000/4000 = 25, but max is 3 → 3
    assert budget.live_max_concurrent() == 3
