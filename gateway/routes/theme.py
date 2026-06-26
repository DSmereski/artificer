"""GET/PUT /v1/theme — the shared UI theme across devices (#182) + Windows accent (#183).

The dashboard PUTs the active theme here whenever the ◑ picker changes it. Other
clients read it: the phone app polls GET on resume and re-skins to match; the
gateway (running on Windows) sets the OS accent color to the theme's accent so
titlebars/taskbar match the wallpaper.

Theme name is one of: holo, terminal, brutalist, vector-tron, glitch-mag, hive-v2.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/v1", tags=["theme"])
log = logging.getLogger("gateway.theme")

_THEMES = {"holo", "terminal", "brutalist", "vector-tron", "glitch-mag", "hive-v2", "joker", "nod",
           "synthwave", "daybreak", "royal"}
_DEFAULT = "hive-v2"

# Each theme's accent as an (R, G, B) for the Windows OS accent color. These are
# DEEPER/MUTED versions of the in-app accents — the taskbar/titlebar use the
# accent as a fill, so the bright app accents read as garish there.
_ACCENT_RGB = {
    "holo":        (16,  104, 120),  # deep teal
    "terminal":    (40,  118, 58),   # forest green
    "brutalist":   (92,  92,  96),   # graphite
    "vector-tron": (44,  78,  168),  # deep electric blue
    "glitch-mag":  (140, 42,  104),  # deep magenta
    "hive-v2":     (150, 100, 36),   # bronze amber
    "joker":       (96,  44,  150),  # deep purple
    "nod":         (150, 30,  30),   # blood red
    "synthwave":   (130, 30,  110),  # deep magenta-violet
    "daybreak":    (30,  100, 100),  # deep teal (muted, light-mode taskbar)
    "royal":       (40,  52,  110),  # deep navy
}


def _store(request: Request):
    return getattr(request.app.state, "crew_store", None)


def _set_windows_accent(theme: str) -> None:
    """Best-effort: set the Windows accent color to the theme's accent (#183).
    Writes the DWM + Explorer accent registry values and broadcasts a settings
    change so titlebars/taskbar recolor. Silent no-op off Windows / on error."""
    rgb = _ACCENT_RGB.get(theme)
    if rgb is None:
        return
    r, g, b = rgb
    abgr = (0xFF << 24) | (b << 16) | (g << 8) | r          # DWM AccentColor = 0xFFBBGGRR
    argb = (0xC4 << 24) | (r << 16) | (g << 8) | b          # ColorizationColor = 0xAARRGGBB
    # AccentPalette: 8 shades; we just repeat the accent (Windows tolerates this).
    palette = "".join(f"{c:02x}" for c in (r, g, b, 0xFF)) * 8
    ps = f"""
$dwm = 'HKCU:\\Software\\Microsoft\\Windows\\DWM'
$acc = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Accent'
Set-ItemProperty -Path $dwm -Name 'AccentColor'          -Value {abgr} -Type DWord -Force
Set-ItemProperty -Path $dwm -Name 'ColorizationColor'    -Value {argb} -Type DWord -Force
Set-ItemProperty -Path $dwm -Name 'ColorizationAfterglow'-Value {argb} -Type DWord -Force
Set-ItemProperty -Path $dwm -Name 'ColorPrevalence'      -Value 1 -Type DWord -Force
if (-not (Test-Path $acc)) {{ New-Item -Path $acc -Force | Out-Null }}
Set-ItemProperty -Path $acc -Name 'AccentColorMenu' -Value {abgr} -Type DWord -Force
Set-ItemProperty -Path $acc -Name 'StartColorMenu'  -Value {abgr} -Type DWord -Force
$bytes = [byte[]] -split ('{palette}' -replace '..','0x$& ')
Set-ItemProperty -Path $acc -Name 'AccentPalette' -Value $bytes -Type Binary -Force
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("windows accent set failed (non-Windows / no perms): %s", e)


# Per-theme Windows Terminal palette: (background, foreground, accent).
_TERM_COLORS = {
    "holo":        ("#0A1418", "#D8EEF2", "#2BD4E8"),
    "terminal":    ("#07140A", "#9BF09B", "#5EE65C"),
    "brutalist":   ("#0A0A0A", "#E8E8E8", "#C8C8C8"),
    "vector-tron": ("#070A18", "#CDD8FF", "#5C8CFF"),
    "glitch-mag":  ("#120A0E", "#F0D8E4", "#E84AB6"),
    "hive-v2":     ("#140F08", "#F0E4D0", "#E8A838"),
    "joker":       ("#120A1A", "#E8F0D8", "#7CE64A"),
    "nod":         ("#0A0606", "#F0D8D8", "#E04040"),
    "synthwave":   ("#0E0A1C", "#EED8F8", "#F040C0"),
    "daybreak":    ("#F5F0E8", "#1C1A14", "#1A7878"),
    "royal":       ("#080C1A", "#F2EDDC", "#D4A820"),
}

# GEN:THEMES START — new shared/8s themes (Sample-design-tokens, additive)
_THEMES |= {"weatherstar", "retro-purple", "inverted", "zombie", "code-fall", "winter", "code-red"}
_ACCENT_RGB.update({
    "weatherstar": (143, 65, 0),
    "retro-purple": (136, 0, 138),
    "inverted": (0, 70, 139),
    "zombie": (0, 132, 0),
    "code-fall": (0, 116, 114),
    "winter": (0, 95, 136),
    "code-red": (138, 0, 0),
})
_TERM_COLORS.update({
    "weatherstar": ("#081221", "#FFFFFF", "#FF932A"),
    "retro-purple": ("#0D0221", "#FFFFFF", "#FF02FF"),
    "inverted": ("#F5E9D7", "#1A1A1A", "#0094FF"),
    "zombie": ("#0A1A0A", "#FFFFFF", "#00FF00"),
    "code-fall": ("#0A0F0A", "#FFFFFF", "#00E4DF"),
    "winter": ("#0A1628", "#FFFFFF", "#02BFFF"),
    "code-red": ("#0F0A0A", "#FFFFFF", "#FF0000"),
})
# GEN:THEMES END

def _set_windows_terminal_theme(theme: str) -> None:
    """Best-effort: write a 'Hive' Windows Terminal color scheme matching the
    theme + make it the default profile scheme (#186). Defensive: backs up
    settings.json and only writes if the edited JSON re-parses, so it can never
    corrupt the user's terminal config."""
    import json as _json
    import re as _re
    colors = _TERM_COLORS.get(theme)
    local = os.environ.get("LOCALAPPDATA", "")
    if not colors or not local:
        return
    settings = Path(local) / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState" / "settings.json"
    if not settings.exists():
        return
    bg, fg, accent = colors
    try:
        raw = settings.read_text(encoding="utf-8-sig")
        # WT settings.json is JSONC: strip /* */ blocks + full-line // comments +
        # trailing commas, then parse. Only FULL-line // (avoids // inside strings).
        clean = _re.sub(r"/\*.*?\*/", "", raw, flags=_re.S)
        clean = _re.sub(r"(?m)^\s*//.*$", "", clean)
        clean = _re.sub(r",(\s*[}\]])", r"\1", clean)
        data = _json.loads(clean)
    except (OSError, ValueError):
        return
    scheme = {
        "name": "Hive", "background": bg, "foreground": fg,
        "cursorColor": accent, "selectionBackground": accent,
        "black": "#0C0C0C", "red": "#C84C4C", "green": "#5EC85E", "yellow": "#D8B84A",
        "blue": "#5C8CFF", "purple": "#C060D0", "cyan": "#40C8D8", "white": fg,
        "brightBlack": "#5A5A5A", "brightRed": "#E86A6A", "brightGreen": "#7CE67C",
        "brightYellow": "#F0D060", "brightBlue": "#7CA8FF", "brightPurple": "#D878E8",
        "brightCyan": "#60E0F0", "brightWhite": "#FFFFFF",
    }
    schemes = data.get("schemes")
    if not isinstance(schemes, list):
        schemes = []
    schemes = [s for s in schemes if not (isinstance(s, dict) and s.get("name") == "Hive")]
    schemes.append(scheme)
    data["schemes"] = schemes
    prof = data.setdefault("profiles", {})
    if isinstance(prof, dict):
        prof.setdefault("defaults", {})["colorScheme"] = "Hive"
    try:
        out = _json.dumps(data, indent=4)
        _json.loads(out)  # paranoia: re-validate before touching disk
        settings.with_suffix(".json.hivebak").write_text(raw, encoding="utf-8")
        settings.write_text(out, encoding="utf-8")
    except (OSError, ValueError):
        return


@router.get("/theme")
async def get_theme(request: Request) -> JSONResponse:
    """Return the active shared theme. Open (the theme name is not sensitive) so
    the loopback dashboard and the paired phone can both read it cheaply."""
    store = _store(request)
    theme = (store.get_meta("ui_theme", _DEFAULT) if store else _DEFAULT) or _DEFAULT
    return JSONResponse({"theme": theme})


@router.put("/theme")
async def put_theme(request: Request, payload: dict = Body(...)) -> JSONResponse:
    """Set the active theme (called by the dashboard picker). Persists it for
    cross-device read + applies the matching Windows accent. Loopback-only write
    (the dashboard is local; remote clients are read-only)."""
    from gateway.deps import _is_loopback
    client = request.client
    if client is None or not _is_loopback(client.host):
        raise HTTPException(403, "theme write is loopback-only")
    name = str(payload.get("theme", "")).strip()
    if name not in _THEMES:
        raise HTTPException(400, f"unknown theme {name!r}; expected one of {sorted(_THEMES)}")
    store = _store(request)
    if store is not None:
        store.set_meta("ui_theme", name)
    _set_windows_accent(name)
    _set_windows_terminal_theme(name)
    # Corsair iCUE RGB → theme accent (#189). Offloaded: the first paint blocks
    # on the SDK handshake, and the whole thing is best-effort.
    import asyncio
    from gateway.helpers import icue
    try:
        await asyncio.to_thread(icue.set_theme, name)
    except Exception as e:  # noqa: BLE001
        log.debug("iCUE theme apply failed: %s", e)
    return JSONResponse({"theme": name, "accent_applied": True})
