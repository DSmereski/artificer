"""Unit tests for the gateway supervisor circuit breaker.

Tests the pure check_and_record() logic in gateway_supervisor.py
without any I/O, subprocess calls, or threading.
"""

from __future__ import annotations

import time

import pytest

from services.scout_daemon.gateway_supervisor import (
    MAX_RESTARTS_PER_HOUR,
    CIRCUIT_WINDOW_S,
    CircuitBreakerState,
    check_and_record,
)


# ---------------------------------------------------------------- helpers

def _state() -> CircuitBreakerState:
    return CircuitBreakerState()


def _monotonic_seq(start: float = 0.0, step: float = 1.0):
    """Generator of monotonically increasing timestamps."""
    t = start
    while True:
        yield t
        t += step


# ---------------------------------------------------------------- basic allow / deny

def test_first_restart_is_always_allowed():
    state = _state()
    allowed, reason = check_and_record(state, now=0.0)
    assert allowed is True
    assert "allowed" in reason
    assert len(state.restart_times) == 1


def test_restarts_up_to_max_are_allowed():
    state = _state()
    seq = _monotonic_seq(start=0.0, step=10.0)
    for i in range(MAX_RESTARTS_PER_HOUR):
        allowed, reason = check_and_record(state, now=next(seq))
        assert allowed is True, f"restart {i + 1} should be allowed; got: {reason}"
    assert len(state.restart_times) == MAX_RESTARTS_PER_HOUR


def test_restart_beyond_max_trips_circuit():
    state = _state()
    seq = _monotonic_seq(start=0.0, step=10.0)
    for _ in range(MAX_RESTARTS_PER_HOUR):
        check_and_record(state, now=next(seq))
    # One more within the same window
    allowed, reason = check_and_record(state, now=next(seq))
    assert allowed is False
    assert state.tripped is True
    assert "circuit open" in reason.lower()


def test_tripped_circuit_stays_closed():
    """Once tripped, every subsequent call returns allowed=False."""
    state = _state()
    seq = _monotonic_seq(start=0.0, step=10.0)
    for _ in range(MAX_RESTARTS_PER_HOUR):
        check_and_record(state, now=next(seq))
    check_and_record(state, now=next(seq))  # trip
    # Several more attempts
    for _ in range(5):
        allowed, reason = check_and_record(state, now=next(seq))
        assert allowed is False
        assert "circuit open" in reason.lower()


# ---------------------------------------------------------------- rolling window expiry

def test_old_restarts_expire_from_window():
    """Restarts older than CIRCUIT_WINDOW_S should fall out of the window,
    allowing fresh restarts again."""
    state = _state()
    window = CIRCUIT_WINDOW_S

    # Fill the window
    seq = _monotonic_seq(start=0.0, step=10.0)
    for _ in range(MAX_RESTARTS_PER_HOUR):
        check_and_record(state, now=next(seq))

    # Advance time past the window so all old entries expire.
    # Note: the circuit is NOT yet tripped (we never exceeded max_restarts).
    assert state.tripped is False

    future_now = window + 100.0  # all old entries are now outside the window
    allowed, reason = check_and_record(state, now=future_now)
    assert allowed is True, f"expired entries should allow new restart; got: {reason}"


def test_partial_window_expiry():
    """If some but not all restarts expire, the count is reduced correctly."""
    state = _state()
    window = CIRCUIT_WINDOW_S

    # Restart at t=0 (will expire)
    check_and_record(state, now=0.0)
    # Two more restarts near end of window (won't expire)
    check_and_record(state, now=window - 20.0)
    check_and_record(state, now=window - 10.0)

    # Now at window+50: the t=0 entry has expired, leaving 2 in the window.
    # With MAX_RESTARTS_PER_HOUR=3, we can do one more.
    if MAX_RESTARTS_PER_HOUR >= 3:
        allowed, _ = check_and_record(state, now=window + 50.0)
        assert allowed is True


# ---------------------------------------------------------------- custom max_restarts

def test_custom_max_restarts_respected():
    state = _state()
    seq = _monotonic_seq(start=0.0, step=5.0)
    # Custom cap of 1
    allowed, _ = check_and_record(state, now=next(seq), max_restarts=1)
    assert allowed is True
    allowed, reason = check_and_record(state, now=next(seq), max_restarts=1)
    assert allowed is False
    assert state.tripped is True


# ---------------------------------------------------------------- deque cap

def test_deque_does_not_grow_unbounded():
    """The internal deque is capped to MAX_RESTARTS_PER_HOUR+1 entries."""
    state = _state()
    cap = MAX_RESTARTS_PER_HOUR + 1
    assert state.restart_times.maxlen == cap
