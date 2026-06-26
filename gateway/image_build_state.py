"""ImageBuildState — explicit slot-filling state for in-progress image builds.

Why this exists: pre-M5 the image-build state was implicit in chat
history. With a 20-message hard cap and helper turn pollution, Hive
forgot subject/aspect/LoRA choices mid-build. Now the state is:

  - explicit (this dataclass — every slot has a name)
  - persistent (mirrored to disk per device)
  - injected into every Planner turn's system prompt so the LLM
    *physically can't* re-ask a filled slot

Lifecycle:
  - Created on the first image-related user turn for a device
  - Updated by Planner's `build_updates` field
  - Cleared on render success, "cancel", reset endpoint, or 30-min
    inactivity
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

log = logging.getLogger("gateway.image_build")


_INACTIVITY_TIMEOUT_S = 30 * 60   # 30 min


@dataclass
class ImageBuildState:
    device_id: str
    started_at: float = field(default_factory=time.time)
    last_touched: float = field(default_factory=time.time)
    subject: str | None = None
    aspect: str | None = None             # portrait/landscape/square/ultrawide
    style_loras: list[str] = field(default_factory=list)
    mood: str | None = None
    negative: str | None = None
    reference_media_id: str | None = None
    count: int = 1
    notes: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- queries

    REQUIRED_SLOTS: tuple[str, ...] = ("subject", "aspect")

    def is_ready(self) -> bool:
        return all(getattr(self, s) for s in self.REQUIRED_SLOTS)

    def is_stale(self, now: float | None = None) -> bool:
        return (now or time.time()) - self.last_touched > _INACTIVITY_TIMEOUT_S

    def render_block(self) -> str:
        """Markdown block for injection into Planner's context.

        Phrased as a hard rule — Planner must NOT re-ask filled slots.
        """
        lines = [
            "## Image being built (do NOT re-ask filled slots)",
            f"  subject: {self.subject or '<unset>'}",
            f"  aspect: {self.aspect or '<unset>'}",
            f"  style_loras: {self.style_loras or '[]'}",
            f"  mood: {self.mood or '<unset>'}",
            f"  reference: {self.reference_media_id or '<none>'}",
            f"  count: {self.count}",
        ]
        if self.notes:
            lines.append(f"  notes: {self.notes!r}")
        if self.is_ready():
            lines.append(
                "All required slots filled — emit [CONFIRM_IMAGE] now."
            )
        else:
            missing = [s for s in self.REQUIRED_SLOTS if not getattr(self, s)]
            lines.append(
                f"Missing required slots: {', '.join(missing)}. "
                "Ask ONE clarifying question via [ASK_USER]."
            )
        return "\n".join(lines)

    # ---------------------------------------------------------------- mutate

    def apply_updates(self, updates: dict) -> list[str]:
        """Apply a Planner-emitted `build_updates` dict.

        Returns the list of slot names that actually changed (for
        logging / event emission).
        """
        changed: list[str] = []
        if not isinstance(updates, dict):
            return changed
        for key, val in updates.items():
            if key not in {"subject", "aspect", "style_loras", "mood",
                           "negative", "reference_media_id", "count", "notes"}:
                continue
            if key == "style_loras" and isinstance(val, list):
                new = [str(x) for x in val if isinstance(x, str)]
                if new != self.style_loras:
                    self.style_loras = new
                    changed.append(key)
            elif key == "notes" and isinstance(val, list):
                new = [str(x) for x in val if isinstance(x, str)]
                if new != self.notes:
                    self.notes = new
                    changed.append(key)
            elif key == "count" and isinstance(val, int) and val > 0:
                if val != self.count:
                    self.count = val
                    changed.append(key)
            elif isinstance(val, str) and val.strip():
                if getattr(self, key) != val:
                    setattr(self, key, val)
                    changed.append(key)
        if changed:
            self.last_touched = time.time()
        return changed


# ---------------------------------------------------------------- store


class ImageBuildStore:
    """Per-device persistent store for ImageBuildState."""

    def __init__(self, root_dir: Path) -> None:
        self._dir = root_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, ImageBuildState] = {}
        self._load_all()

    def _path(self, device_id: str) -> Path:
        # device_id may contain hex; restrict filename charset for safety.
        safe = "".join(c for c in device_id if c.isalnum() or c in ("-", "_"))
        if not safe:
            safe = "unknown"
        return self._dir / f"{safe[:64]}.json"

    def _load_all(self) -> None:
        for path in self._dir.glob("*.json"):
            try:
                obj = json.loads(path.read_text(encoding="utf-8"))
                state = ImageBuildState(**obj)
                if state.is_stale():
                    path.unlink(missing_ok=True)
                    continue
                self._cache[state.device_id] = state
            except (OSError, json.JSONDecodeError, TypeError) as e:
                log.warning("dropping malformed build state %s: %s", path, e)
                path.unlink(missing_ok=True)

    def get(self, device_id: str) -> ImageBuildState | None:
        st = self._cache.get(device_id)
        if st is None:
            return None
        if st.is_stale():
            self.clear(device_id)
            return None
        return st

    def get_or_create(self, device_id: str) -> ImageBuildState:
        st = self.get(device_id)
        if st is None:
            st = ImageBuildState(device_id=device_id)
            self._cache[device_id] = st
            self._persist(st)
        return st

    def update(self, device_id: str, updates: dict) -> list[str]:
        st = self.get_or_create(device_id)
        changed = st.apply_updates(updates)
        if changed:
            self._persist(st)
        return changed

    def clear(self, device_id: str) -> None:
        self._cache.pop(device_id, None)
        self._path(device_id).unlink(missing_ok=True)

    def cleanup_stale(self) -> int:
        """Drop everything past the inactivity timeout. Returns how many."""
        now = time.time()
        dropped = 0
        for did in list(self._cache.keys()):
            if self._cache[did].is_stale(now):
                self.clear(did)
                dropped += 1
        return dropped

    def _persist(self, state: ImageBuildState) -> None:
        try:
            self._path(state.device_id).write_text(
                json.dumps(asdict(state), indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            log.warning("failed to persist build state %s: %s",
                        state.device_id, e)
