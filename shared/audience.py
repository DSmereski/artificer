"""Audience clamping for vault writes.

A device's audience is its scope of authority — `[all]` is privileged
(can write any audience), anything narrower clamps the writes it
authorises. This module is the single source of truth for that
intersection so a fix or a test only has to land in one place.

Background: the same clamp logic was duplicated at
`routes/chat.py:_handle_remember` and `action_executor.py::vault_learn`,
which the 2026-04-29 code-quality review flagged as a security-relevant
DRY violation — a tightening fix landing in only one site would create
a privilege-escalation gap on the other.

Rules (in order):
  - device_audience is `None` or empty                  → unclamped
    (legacy callers without audience metadata are trusted; the route
    layer already enforces auth, this is defence-in-depth).
  - device_audience contains "all"                      → unclamped
    (privileged tokens, e.g. the desktop dev seat).
  - otherwise                                           → intersect
    requested ∩ device. If the intersection is empty, fall back to
    `list(device_audience)` so the write isn't silently dropped — it
    just lands at the device's own audience instead of escalating to
    whatever was requested.
"""

from __future__ import annotations

from collections.abc import Iterable


def clamp_audience(
    requested: Iterable[str] | None,
    device_audience: Iterable[str] | None,
) -> list[str]:
    """Intersect `requested` with `device_audience`.

    Returns a fresh list — never the input objects — so callers can
    safely mutate the result.
    """
    req = list(requested or [])
    dev = list(device_audience or [])
    if not dev or "all" in dev:
        return req
    intersected = [a for a in req if a in dev]
    return intersected or list(dev)
