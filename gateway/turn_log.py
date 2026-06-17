"""Per-turn structured chat log for troubleshooting (M6.3+).

Every hive turn appends one JSON object to a daily JSONL file at
  <state_dir>/turn-logs/YYYY-MM-DD.jsonl

What we capture (so the user can debug "why did Terry say X?"):
  - timestamp, turn_id, device_id, bot
  - the user_msg verbatim
  - planner.summary + delegations + raw_preview + error
  - per-helper: role, model, latency_ms, tokens_in/out, output_summary,
    raw_preview (capped 500 chars), error, citations count
  - synthesis: reply + actions + error + raw_preview
  - executor receipts: which actions ran, ok/fail + detail
  - final reply (the text the user actually saw)
  - totals: latency, tokens
  - flags: blocked (critic), used_cpu (which helpers fell back)

JSONL format → `jq`/`grep`/`tail -f` work out of the box.
Endpoint `/v1/telemetry/turn_log` returns the tail for the app's dev panel.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("gateway.turn_log")


_PREVIEW_LIMIT = 500


def _preview(text: str | None) -> str:
    """Truncate + scrub a string before it lands on disk.

    `scrub_secrets` redacts API keys / tokens / homelab IPs so that an
    LLM that echoes a credential in its chain-of-thought doesn't leak
    it to the JSONL log.
    """
    if text is None:
        return ""
    try:
        from vault_writer.util import scrub_secrets
        s = scrub_secrets(str(text))
    except Exception:  # noqa: BLE001
        s = str(text)
    if len(s) <= _PREVIEW_LIMIT:
        return s
    return s[:_PREVIEW_LIMIT] + f"…[+{len(s) - _PREVIEW_LIMIT} chars]"


@dataclass
class HelperLogEntry:
    role: str
    model: str
    latency_ms: int
    tokens_in: int
    tokens_out: int
    output_summary: str = ""
    raw_preview: str = ""
    error: str | None = None
    citations: int = 0
    confidence: str | None = None
    used_cpu: bool = False


@dataclass
class TurnLogEntry:
    ts: float = field(default_factory=time.time)
    turn_id: str = ""
    device_id: str = ""
    user_id: int = 0
    bot: str = "terry"
    user_msg: str = ""
    planner_summary: str = ""
    planner_raw_preview: str = ""
    planner_error: str | None = None
    delegations: list[str] = field(default_factory=list)
    helpers: list[HelperLogEntry] = field(default_factory=list)
    synth_reply: str = ""
    synth_raw_preview: str = ""
    synth_error: str | None = None
    # One of: "compose" | "fallback" | "prose-rescue" | "coordinator-bypass"
    synth_mode: str = "coordinator-bypass"
    actions: list[dict] = field(default_factory=list)
    receipts: list[dict] = field(default_factory=list)
    final_reply: str = ""
    blocked: bool = False
    total_tokens: int = 0
    total_latency_ms: int = 0

    def to_jsonable(self) -> dict:
        return {
            "ts": self.ts,
            "turn_id": self.turn_id,
            "device_id": self.device_id,
            "user_id": self.user_id,
            "bot": self.bot,
            "user_msg": self.user_msg,
            "planner": {
                "summary": self.planner_summary,
                "raw_preview": self.planner_raw_preview,
                "error": self.planner_error,
            },
            "delegations": self.delegations,
            "helpers": [h.__dict__ for h in self.helpers],
            "synthesis": {
                "reply": self.synth_reply,
                "raw_preview": self.synth_raw_preview,
                "error": self.synth_error,
                "mode": self.synth_mode,
                "actions": self.actions,
            },
            "receipts": self.receipts,
            "final_reply": self.final_reply,
            "blocked": self.blocked,
            "total_tokens": self.total_tokens,
            "total_latency_ms": self.total_latency_ms,
        }


class TurnLogStore:
    """Append-only, thread-safe per-day JSONL with an in-memory tail."""

    def __init__(self, root: Path, *, mem_cap: int = 100) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._recent: deque[dict] = deque(maxlen=mem_cap)

    def _path_for(self, ts: float) -> Path:
        day = time.strftime("%Y-%m-%d", time.gmtime(ts))
        return self._root / f"{day}.jsonl"

    def append(self, entry: TurnLogEntry) -> None:
        """Synchronous append. Writes file + memory ring under lock."""
        payload = entry.to_jsonable()
        line = json.dumps(payload, default=str)
        path = self._path_for(entry.ts)
        with self._lock:
            try:
                with path.open("a", encoding="utf-8") as f:
                    f.write(line)
                    f.write("\n")
            except OSError as e:
                log.warning("turn-log write failed: %s", e)
            self._recent.append(payload)

    async def append_async(self, entry: TurnLogEntry) -> None:
        """Async wrapper — runs append() in the default thread executor
        so slow disks can't stall the chat WS event loop."""
        import asyncio
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.append, entry)

    def tail(self, n: int = 20) -> list[dict]:
        with self._lock:
            return list(self._recent)[-n:]

    def files(self) -> list[Path]:
        return sorted(self._root.glob("*.jsonl"))


# ---------------------------------------------------------------- helpers


def _scrub(text: str | None, limit: int | None = None) -> str:
    """Like `_preview` but with explicit length cap. Always scrubs."""
    if text is None:
        return ""
    try:
        from vault_writer.util import scrub_secrets
        s = scrub_secrets(str(text))
    except Exception:  # noqa: BLE001
        s = str(text)
    if limit is not None and len(s) > limit:
        s = s[:limit]
    return s


def helper_entries_from_results(
    results: list, used_cpu_roles: set[str] | None = None,
) -> list[HelperLogEntry]:
    used_cpu_roles = used_cpu_roles or set()
    out: list[HelperLogEntry] = []
    for r in results:
        if r is None:
            continue
        out_dict = r.output if isinstance(r.output, dict) else {}
        summary = out_dict.get("summary", "")
        out.append(HelperLogEntry(
            role=r.role,
            model=getattr(r, "model_id", ""),
            latency_ms=getattr(r, "latency_ms", 0),
            tokens_in=getattr(r, "tokens_in", 0),
            tokens_out=getattr(r, "tokens_out", 0),
            output_summary=_scrub(summary, limit=240),
            raw_preview=_preview(json.dumps(out_dict, default=str)),
            error=_scrub(getattr(r, "error", None)) or None,
            citations=len(getattr(r, "citations", []) or []),
            confidence=getattr(r, "confidence", None),
            used_cpu=r.role in used_cpu_roles,
        ))
    return out
