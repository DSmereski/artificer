"""Gateway HTTP health supervisor with circuit breaker.

Polls GET /health (or /board/state as fallback) on a configurable
interval. After N consecutive failures the gateway is restarted via
start-gateway.ps1. A circuit breaker caps restarts to MAX_RESTARTS_PER_HOUR
in any rolling 60-minute window; beyond that limit the supervisor stops
trying and fires an ntfy alert so the operator can investigate.

The restart behaviour is gated by the `gateway_autorestart` bool in
services.scout_daemon.config (default True) so it can be disabled in
environments where the gateway is managed externally.

Design notes
------------
- _CircuitBreaker is a pure function / dataclass — no subprocess calls,
  no threading — so it is unit-testable without mocks.
- The daemon's _supervisor_loop drives it; that loop is the only caller
  of the actual restart side-effect.
- The circuit breaker uses a rolling window (deque of UTC timestamps)
  rather than a fixed-clock hour bucket so it cannot be gamed by a
  restart exactly on the hour boundary.
"""

from __future__ import annotations

import collections
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# Max restarts allowed within CIRCUIT_WINDOW_S seconds.
MAX_RESTARTS_PER_HOUR: int = 3
CIRCUIT_WINDOW_S: float = 3600.0  # rolling 60-minute window

# Number of consecutive health-check failures before triggering a restart.
FAIL_THRESHOLD: int = 3

# Seconds between each health probe.
PROBE_INTERVAL_S: float = 45.0

# The health endpoint(s) to probe (tried in order, first 200 wins).
_HEALTH_URLS = (
    "http://127.0.0.1:8766/health",
    "http://127.0.0.1:8766/board/state",
)


# ---------------------------------------------------------------- circuit breaker

@dataclass
class CircuitBreakerState:
    """Mutable state for the rolling-window circuit breaker.

    Kept as a plain dataclass so tests can construct it directly and
    call check_and_record() without any I/O.
    """
    # Timestamps (monotonic, seconds) of recent restart events.
    restart_times: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=MAX_RESTARTS_PER_HOUR + 1)
    )
    # True once the circuit is open (max restarts exhausted).
    tripped: bool = False


def check_and_record(
    state: CircuitBreakerState,
    *,
    now: float | None = None,
    window_s: float = CIRCUIT_WINDOW_S,
    max_restarts: int = MAX_RESTARTS_PER_HOUR,
) -> tuple[bool, str]:
    """Check whether a restart is allowed, then record it if so.

    Returns (allowed: bool, reason: str).

    If allowed=True the restart timestamp is recorded into state.restart_times.
    If allowed=False the circuit is tripped (state.tripped = True).

    This function is pure from a side-effect perspective (no I/O, no
    subprocess) so it can be tested synchronously.
    """
    if state.tripped:
        return False, "circuit open: max restarts already exhausted"

    t = now if now is not None else time.monotonic()

    # Drop entries outside the rolling window.
    cutoff = t - window_s
    while state.restart_times and state.restart_times[0] < cutoff:
        state.restart_times.popleft()

    in_window = len(state.restart_times)
    if in_window >= max_restarts:
        state.tripped = True
        return (
            False,
            f"circuit open: {in_window} restarts in last {window_s / 60:.0f}m "
            f"(max {max_restarts})",
        )

    state.restart_times.append(t)
    remaining = max_restarts - (in_window + 1)
    return True, f"restart allowed ({in_window + 1}/{max_restarts} in window; {remaining} remaining)"


# ---------------------------------------------------------------- health probe

def probe_gateway(timeout_s: float = 5.0) -> bool:
    """Return True if any health URL responds with HTTP 200."""
    for url in _HEALTH_URLS:
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            continue
    return False
