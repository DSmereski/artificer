"""Per-turn telemetry ring buffer (M6.3).

The HiveCoordinator records each completed turn here. The
`/v1/telemetry/last_turn` endpoint surfaces the buffer to the app's
dev panel.

P4 (prompt-version telemetry): adds ``prompt_version(text)`` — a
SHA-256 content hash (first 12 hex chars) cached per unique prompt text
— and stamps it onto each ``TurnRecord`` so behaviour can be attributed
to a specific prompt revision and A/B-compared across revisions/models.
"""

from __future__ import annotations

import hashlib
import threading
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from functools import lru_cache


# ---------------------------------------------------------------------------
# P4 — prompt versioning
# ---------------------------------------------------------------------------

@lru_cache(maxsize=512)
def prompt_version(text: str) -> str:
    """Return a stable 12-char SHA-256 hex tag for *text*.

    The same string always maps to the same tag.  Different strings
    produce different tags (collision probability negligible at 12 hex
    chars = 48 bits).  Results are cached in-process via ``lru_cache``
    so repeated calls for the same prompt text are O(1) dict lookups.

    >>> prompt_version("hello") == prompt_version("hello")
    True
    >>> prompt_version("hello") != prompt_version("world")
    True
    """
    digest = hashlib.sha256(text.encode()).hexdigest()
    return digest[:12]


# ---------------------------------------------------------------------------
# Telemetry record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TurnRecord:
    ts: float
    turn_id: str
    bot: str
    user_msg_preview: str       # truncated to 240 chars
    helpers_used: list[str]
    total_tokens: int
    total_latency_ms: int
    blocked: bool
    error: str | None
    actions: list[str]          # verbs only (no payloads)
    planner_prompt_version: str = ""   # content hash of prompts/planner.md (legacy)
    # P4: general per-turn prompt version tag (nullable/empty = not stamped)
    prompt_version: str = ""


# ---------------------------------------------------------------------------
# Telemetry buffer
# ---------------------------------------------------------------------------

class TurnTelemetry:
    """Thread-safe FIFO ring buffer of the last N turns."""

    def __init__(self, max_records: int = 100) -> None:
        self._records: deque[TurnRecord] = deque(maxlen=max_records)
        self._lock = threading.Lock()

    def record(self, rec: TurnRecord) -> None:
        with self._lock:
            self._records.append(rec)

    def last(self, n: int = 20) -> list[TurnRecord]:
        with self._lock:
            return list(self._records)[-n:]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def to_jsonable(self, n: int = 20) -> list[dict]:
        return [asdict(r) for r in self.last(n)]

    # --- P4 query helpers ---------------------------------------------------

    def query_by_prompt_version(self, version_tag: str) -> list[TurnRecord]:
        """Return all buffered records whose ``prompt_version`` matches *version_tag*.

        Supports A/B comparison: call once per revision tag and compare
        the resulting record sets (tokens, latency, errors, etc.).
        """
        with self._lock:
            return [r for r in self._records if r.prompt_version == version_tag]

    def group_by_prompt_version(self) -> dict[str, list[TurnRecord]]:
        """Return a mapping of ``prompt_version`` tag → list of ``TurnRecord``.

        Empty-string tags (un-versioned turns) are included under the
        ``""`` key so callers can see legacy vs. versioned traffic.

        Example::

            groups = telemetry.group_by_prompt_version()
            for tag, records in groups.items():
                print(tag, len(records))
        """
        with self._lock:
            result: dict[str, list[TurnRecord]] = defaultdict(list)
            for r in self._records:
                result[r.prompt_version].append(r)
            return dict(result)
