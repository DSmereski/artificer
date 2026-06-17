"""Per-turn telemetry ring buffer (M6.3).

The HiveCoordinator records each completed turn here. The
`/v1/telemetry/last_turn` endpoint surfaces the buffer to the app's
dev panel.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field


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
    planner_prompt_version: str = ""   # content hash of prompts/planner.md


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
