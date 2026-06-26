"""Corsair iCUE RGB → follows the active UI theme (#189).

The dashboard ◑ picker PUTs a theme to ``/v1/theme``; this paints every LED on
every connected Corsair device with that theme's accent so the keyboard/mouse
match the wallpaper (same family as the Windows accent #183 and Terminal #186).

Persistence: an iCUE SDK colour only holds while the SDK *session stays open*.
The gateway is long-running, so we keep ONE module-level session alive — colours
persist until the gateway exits (then iCUE restores the user's profile).

Everything is best-effort. If iCUE isn't running, the ``cuesdk`` package is
missing, SDK access is disabled, or we're off-Windows, every call is a silent
no-op — a lighting failure must never break the theme PUT.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("gateway.icue")

# Bright, saturated per-theme accents — keyboards read washed-out with the muted
# Windows-accent values, so these mirror the in-app accent (see _TERM_COLORS in
# routes/theme.py) rather than the OS accent.
_ICUE_RGB = {
    "holo":        (43,  212, 232),  # cyan
    "terminal":    (94,  230, 92),   # green
    "brutalist":   (200, 200, 200),  # white-grey
    "vector-tron": (92,  140, 255),  # electric blue
    "glitch-mag":  (232, 74,  182),  # magenta
    "hive-v2":     (232, 168, 56),   # amber
    "joker":       (124, 230, 74),   # acid green
    "nod":         (224, 64,  64),   # red
    "synthwave":   (240, 48,  188),  # hot magenta-pink
    "daybreak":    (26,  188, 188),  # bright teal
    "royal":       (212, 168, 32),   # vivid gold
}

# GEN:ICUE START — new shared/8s themes (Sample-design-tokens, additive)
_ICUE_RGB.update({
    "weatherstar": (255, 147, 42),
    "retro-purple": (255, 2, 255),
    "inverted": (0, 148, 255),
    "zombie": (0, 255, 0),
    "code-fall": (0, 228, 223),
    "winter": (2, 191, 255),
    "code-red": (255, 0, 0),
})
# GEN:ICUE END
_lock = threading.Lock()
_sdk = None              # live CueSdk instance once connected
_connected = False
_last_rgb: tuple[int, int, int] | None = None


def _ensure_locked() -> bool:
    """Connect to the iCUE SDK if not already. Caller must hold ``_lock``.
    Returns True when a live session exists."""
    global _sdk, _connected
    if _connected and _sdk is not None:
        return True
    try:
        from cuesdk import CueSdk, CorsairSessionState
    except Exception as e:  # noqa: BLE001 — package not installed
        log.debug("iCUE: cuesdk unavailable: %s", e)
        return False
    try:
        sdk = CueSdk()
        state = {"v": None}
        err = sdk.connect(lambda evt: state.__setitem__("v", evt.state))
        for _ in range(50):  # up to ~5s for iCUE to accept the session
            if state["v"] == CorsairSessionState.CSS_Connected:
                break
            time.sleep(0.1)
        if state["v"] != CorsairSessionState.CSS_Connected:
            log.debug("iCUE: not connected (state=%s, err=%s) — iCUE running + SDK enabled?",
                      state["v"], err)
            return False
        _sdk = sdk
        _connected = True
        log.info("iCUE SDK connected")
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("iCUE: connect failed: %s", e)
        return False


def set_color(r: int, g: int, b: int) -> bool:
    """Paint every LED on every Corsair device. Best-effort; never raises.
    Blocks up to ~5s on the FIRST call (session handshake), fast thereafter, so
    callers on the event loop should offload via ``asyncio.to_thread``."""
    global _last_rgb, _connected
    with _lock:
        if not _ensure_locked():
            return False
        try:
            from cuesdk import CorsairLedColor, CorsairDeviceFilter
            devs, _e = _sdk.get_devices(CorsairDeviceFilter(device_type_mask=0xFFFFFFFF))
            if not devs:
                return False
            painted = 0
            for d in devs:
                pos, _pe = _sdk.get_led_positions(d.device_id)
                if not pos:
                    continue
                cols = [CorsairLedColor(id=p.id, r=r, g=g, b=b, a=255) for p in pos]
                _sdk.set_led_colors(d.device_id, cols)
                painted += 1
            _last_rgb = (r, g, b)
            log.debug("iCUE: painted %d device(s) rgb=(%d,%d,%d)", painted, r, g, b)
            return painted > 0
        except Exception as e:  # noqa: BLE001
            log.debug("iCUE: set_color failed, dropping session: %s", e)
            _connected = False  # force reconnect next call
            return False


def set_theme(theme: str) -> bool:
    """Paint the devices with ``theme``'s accent. Unknown theme → no-op."""
    rgb = _ICUE_RGB.get(theme)
    if rgb is None:
        return False
    return set_color(*rgb)
