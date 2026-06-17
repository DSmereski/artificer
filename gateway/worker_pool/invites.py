"""Short-lived invite codes for adding hive nodes.

Mirrors gateway.auth.PairingBroker but with 6-digit numeric codes —
the spec calls for a human-friendly format like '814-273' that's easy
to type into a wizard. Single-use, TTL'd, no persistence (codes live
in-memory; gateway restart invalidates all pending invites).
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass


_DIGITS = "0123456789"


@dataclass(frozen=True, slots=True)
class NodeInvite:
    code: str               # display form, e.g. "814-273"
    created_at: float
    expires_at: float


def _gen_six_digits() -> str:
    raw = "".join(secrets.choice(_DIGITS) for _ in range(6))
    return f"{raw[:3]}-{raw[3:]}"


def _normalise(code: str) -> str:
    """Strip whitespace + dashes for comparison."""
    return code.strip().replace("-", "")


class InviteBroker:
    """Issue + claim 6-digit invite codes. In-memory; not durable across restart."""

    def __init__(self, ttl_seconds: int = 600) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._pending: dict[str, NodeInvite] = {}  # key: normalised digits

    def issue(self) -> NodeInvite:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            # Loop until we get a unique code (collisions extremely rare
            # at 10^6 codespace, but be defensive).
            for _ in range(10):
                code = _gen_six_digits()
                key = _normalise(code)
                if key not in self._pending:
                    invite = NodeInvite(
                        code=code,
                        created_at=now,
                        expires_at=now + self._ttl,
                    )
                    self._pending[key] = invite
                    return invite
            raise RuntimeError("invite code collision storm — try again")

    def claim(self, code: str) -> bool:
        key = _normalise(code)
        now = time.time()
        with self._lock:
            # _prune_locked already removed any invite whose expires_at
            # is in the past, so a successful pop() is guaranteed to be
            # a live invite.
            self._prune_locked(now)
            return self._pending.pop(key, None) is not None

    def revoke(self, code: str) -> bool:
        key = _normalise(code)
        with self._lock:
            return self._pending.pop(key, None) is not None

    def list_active(self) -> list[NodeInvite]:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            return list(self._pending.values())

    def _prune_locked(self, now: float) -> None:
        stale = [k for k, inv in self._pending.items() if inv.expires_at <= now]
        for k in stale:
            del self._pending[k]
