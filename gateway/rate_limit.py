"""Per-device token-bucket rate limits.

Applied at the dependency boundary so a single runaway client can't
poison image-gen, vault writes, or chat.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class _Bucket:
    capacity: float
    refill_per_second: float
    tokens: float
    last_refill: float = field(default_factory=time.monotonic)

    def try_consume(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
            self.last_refill = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


class RateLimiter:
    """Named token buckets keyed by (device_id, bucket_name).

    Thread-safe. Designed for in-process use; restart resets state (acceptable
    for a single-user workstation gateway).
    """

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._lock = threading.Lock()
        # Per-instance config so `register()` can't mutate other instances
        # (the audit caught a class-level dict bug here). Defaults can be
        # overridden via `register()` or `configure()`.
        self._configs: dict[str, tuple[int, int]] = {
            "writes": (60, 60),
            "images": (30, 6),
            "calendar": (30, 10),         # 30 jobs/min, burst 10
            "vault_actions": (30, 10),    # synthesis-emitted writes
            "lora_imports": (1, 5),       # ~1 every 60s, burst 5 (≈ short flurries OK, sustained slow)
        }

    def register(self, name: str, *, per_minute: int, burst: int | None = None) -> None:
        """Stored alongside the limiter so routes can just name-reference."""
        self._configs[name] = (per_minute, burst or per_minute)

    def try_acquire(self, device_id: str, bucket_name: str) -> bool:
        cfg = self._configs.get(bucket_name)
        if cfg is None:
            return True
        per_minute, burst = cfg
        refill = per_minute / 60.0
        key = (device_id, bucket_name)
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(capacity=float(burst), refill_per_second=refill, tokens=float(burst))
                self._buckets[key] = bucket
            return bucket.try_consume()

    def configure(self, writes_per_minute: int, images_per_hour: int) -> None:
        self._configs["writes"] = (writes_per_minute, writes_per_minute)
        # Image bucket refills on a 1-hour window; translate to per-minute equivalent.
        # burst=max(6, images_per_hour // 4) so short flurries still work.
        burst = max(6, images_per_hour // 4)
        self._configs["images"] = (images_per_hour // 60 if images_per_hour >= 60 else 1, burst)
        # Vault read bucket: full-vault scans (backlinks, tags, by-tag,
        # related) walk every .md on every call. A paired-but-low-trust
        # device looping these would peg IO. 60/min with a burst of 30
        # is generous for normal browsing yet caps the worst case.
        self._configs.setdefault("vault_reads", (60, 30))
        # Vault writes bucket used by the synthesizer's vault_learn
        # (pre-existing under that name; bucket auto-created on first
        # try_acquire if missing).
        self._configs.setdefault("vault_actions", (writes_per_minute, writes_per_minute))
