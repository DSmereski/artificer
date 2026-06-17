"""Device pairing, token issuance, and auth middleware for the gateway.

Security model:
  * Pairing: ephemeral code valid for a short TTL. Scanning the QR on the phone
    posts the code back to /v1/pair and receives a per-device 256-bit token.
  * Tokens: stored server-side as sha256 only — no recoverability. Revocation
    is a row flip. A device chooses its own display name at pair time.
  * Rate limits and replay protection are implemented at the route layer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Device:
    id: str
    name: str
    token_hash: str
    audience: tuple[str, ...]
    created: float
    last_seen: float
    revoked: bool = False
    # Logical user this device belongs to. All of David's devices
    # default to "owner" so chat history, vault context, and ntfy
    # routing are SHARED across the phone + PC + future tablet.
    # Routes derive `user_id` from `device.user` (md5 → int), so
    # any two devices with the same `user` value see the same chat
    # log. Multi-tenant deployments would issue distinct values.
    user: str = "owner"


@dataclass(slots=True)
class _PendingCode:
    code: str
    expires: float


class DeviceStore:
    """JSON-backed store of paired devices. In-memory + atomic write on change."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._devices: dict[str, Device] = {}
        self._load()

    # ---------------------------------------------------------------- persistence

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, list):
            return
        for row in data:
            if not isinstance(row, dict):
                continue
            try:
                d = Device(
                    id=str(row["id"]),
                    name=str(row.get("name", "unnamed")),
                    token_hash=str(row["token_hash"]),
                    audience=tuple(row.get("audience") or ["all"]),
                    created=float(row.get("created", 0.0)),
                    last_seen=float(row.get("last_seen", 0.0)),
                    revoked=bool(row.get("revoked", False)),
                    # Backfill: existing devices.json predates the
                    # `user` field; default to "owner" for the
                    # single-user system so chat sync starts
                    # working immediately on next restart.
                    user=str(row.get("user", "owner")),
                )
                self._devices[d.id] = d
            except (KeyError, ValueError, TypeError):
                continue

    def _persist_locked(self) -> None:
        # Devices store is critical — losing this on a power-cut means
        # the user has to re-pair every device. atomic_write_json gives
        # us flush + fsync.
        from shared.atomic_write import atomic_write_json
        atomic_write_json(
            self._path,
            [asdict(d) for d in self._devices.values()],
        )

    # ---------------------------------------------------------------- public API

    def list(self) -> list[Device]:
        with self._lock:
            return list(self._devices.values())

    def list_active(self) -> list[Device]:
        """Live (non-revoked) devices only. Distinct from `list()` —
        callers using this name (chat.py audience clamp lookup, the
        cross-device user-id resolution path) MUST NOT see revoked
        devices, otherwise a stale token can re-poison the audience
        view of an active session."""
        with self._lock:
            return [d for d in self._devices.values() if not d.revoked]

    def add(
        self, *, name: str, token: str,
        audience: tuple[str, ...] = ("all",),
        user: str = "owner",
    ) -> Device:
        device_id = secrets.token_urlsafe(12)
        now = time.time()
        device = Device(
            id=device_id,
            name=name or f"device-{device_id[:6]}",
            token_hash=_hash_token(token),
            audience=audience,
            created=now,
            last_seen=now,
            revoked=False,
            user=user,
        )
        with self._lock:
            self._devices[device_id] = device
            self._persist_locked()
        return device

    def touch(self, device_id: str) -> None:
        with self._lock:
            d = self._devices.get(device_id)
            if d is None or d.revoked:
                return
            self._devices[device_id] = Device(
                id=d.id, name=d.name, token_hash=d.token_hash,
                audience=d.audience, created=d.created,
                last_seen=time.time(), revoked=d.revoked,
                user=d.user,
            )
            self._persist_locked()

    def revoke(self, device_id: str) -> bool:
        """Soft-revoke: keep the row but mark it dead. Useful for audit
        trails. Most callers should prefer `purge` — see below."""
        with self._lock:
            d = self._devices.get(device_id)
            if d is None:
                return False
            self._devices[device_id] = Device(
                id=d.id, name=d.name, token_hash=d.token_hash,
                audience=d.audience, created=d.created,
                last_seen=d.last_seen, revoked=True,
                user=d.user,
            )
            self._persist_locked()
            return True

    def purge(self, device_id: str) -> bool:
        """Hard-remove the device row. The DELETE /v1/devices/{id} route
        uses this so the user's paired-device list doesn't accrue
        revoked entries forever — every pairing test and every old
        revoked phone would otherwise live forever in devices.json."""
        with self._lock:
            if device_id not in self._devices:
                return False
            del self._devices[device_id]
            self._persist_locked()
            return True

    def purge_revoked(self) -> int:
        """Drop every currently-revoked device. Returns the count."""
        with self._lock:
            ids = [k for k, v in self._devices.items() if v.revoked]
            for k in ids:
                del self._devices[k]
            if ids:
                self._persist_locked()
            return len(ids)

    def purge_by_name_prefix(self, prefixes: list[str]) -> int:
        """Drop every device whose `name` starts with any of `prefixes`.
        Used by the gateway's startup hook + the smoke harness to keep
        transient `smoke`, `vault-smoke`, `video-smoke`, … devices from
        cluttering the list."""
        with self._lock:
            ids = [
                k for k, v in self._devices.items()
                if any(v.name.startswith(p) for p in prefixes)
            ]
            for k in ids:
                del self._devices[k]
            if ids:
                self._persist_locked()
            return len(ids)

    def verify(self, token: str) -> Device | None:
        """Constant-time match of `token` against any active device."""
        candidate_hash = _hash_token(token)
        with self._lock:
            for d in self._devices.values():
                if d.revoked:
                    continue
                if hmac.compare_digest(d.token_hash, candidate_hash):
                    return d
        return None


class PairingBroker:
    """Short-lived pairing codes. Single-use, TTL'd."""

    def __init__(self, ttl_seconds: int = 300, code_length: int = 8) -> None:
        self._ttl = ttl_seconds
        self._code_length = code_length
        self._lock = threading.Lock()
        self._pending: dict[str, _PendingCode] = {}   # code -> record

    def issue(self) -> str:
        """Generate a new pairing code."""
        code = _short_code(self._code_length)
        with self._lock:
            self._prune_locked()
            self._pending[code] = _PendingCode(
                code=code, expires=time.time() + self._ttl
            )
        return code

    def claim(self, code: str) -> bool:
        """Consume `code` if valid and not expired. Returns True on success."""
        with self._lock:
            self._prune_locked()
            rec = self._pending.pop(code, None)
            return rec is not None and rec.expires > time.time()

    def _prune_locked(self) -> None:
        now = time.time()
        stale = [c for c, r in self._pending.items() if r.expires <= now]
        for c in stale:
            del self._pending[c]


# Unambiguous alphabet for the human-typed pairing code (no 0/O, 1/I/L).
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _short_code(length: int) -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))


def issue_token(nbytes: int = 32) -> str:
    """Generate a URL-safe token of the requested byte strength."""
    return secrets.token_urlsafe(nbytes)
