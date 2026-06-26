"""GPU policy — may AI borrow the RTX 4080 (GPU 0)?

David's rule (2026-06-21): the 4080 is gaming-first, but AI MAY borrow it when
no game is running. A game starting evacuates AI from it. A manual switch
overrides the auto behaviour (the "off switch").

  mode = "auto"      : AI may use the 4080 only when NOT gaming (default)
  mode = "force_on"  : AI may always use the 4080 (ignore gaming)
  mode = "force_off" : AI never uses the 4080 (reserve it for gaming always)

The 5060 Tis (GPU 1 & 2) are ALWAYS AI. `ai_devices()` returns the
CUDA_VISIBLE_DEVICES string AI workloads should use under the current policy.
Gaming is detected best-effort via the scout-daemon snapshot; detection failure
falls back to "not gaming" (the manual switch is the authoritative guard).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from gateway.sysmon_client import fetch_snapshot

log = logging.getLogger("gateway.gpu_policy")

_AI_GPUS = "1,2"          # the two 5060 Tis — always AI
_ALL_GPUS = "0,1,2"       # + the 4080 when policy allows
_GAMING_GPU = 0           # the 4080
VALID_MODES = ("auto", "force_on", "force_off")

_STATE_FILE = Path(__file__).resolve().parent.parent / "state" / "gpu_mode.json"


def get_mode() -> str:
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        m = str(data.get("mode", "auto"))
        return m if m in VALID_MODES else "auto"
    except Exception:  # noqa: BLE001 — missing/corrupt file => default
        return "auto"


def set_mode(mode: str) -> str:
    if mode not in VALID_MODES:
        raise ValueError(f"invalid mode {mode!r}; must be one of {VALID_MODES}")
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps({"mode": mode}), encoding="utf-8")
    return mode


async def is_gaming() -> bool:
    """True if a game is running on the 4080 (best-effort). Detection failure
    -> False; the manual switch is the authoritative guard."""
    snap = await fetch_snapshot()
    if not snap or not snap.get("game_running"):
        return False
    gpu = snap.get("game_gpu")
    if gpu is None:               # game running, GPU unknown -> assume the 4080
        return True
    try:
        return int(gpu) == _GAMING_GPU
    except (TypeError, ValueError):
        return True


async def ai_may_use_4080() -> bool:
    mode = get_mode()
    if mode == "force_on":
        return True
    if mode == "force_off":
        return False
    return not await is_gaming()   # auto


async def ai_devices() -> str:
    """CUDA_VISIBLE_DEVICES for AI workloads under the current policy."""
    return _ALL_GPUS if await ai_may_use_4080() else _AI_GPUS


async def status() -> dict:
    mode = get_mode()
    gaming = await is_gaming()
    may = (mode == "force_on") or (mode != "force_off" and not gaming)
    return {
        "mode": mode,
        "gaming": gaming,
        "ai_may_use_4080": may,
        "ai_devices": _ALL_GPUS if may else _AI_GPUS,
    }
