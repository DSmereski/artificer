"""Process watchdog: detect Terry/gateway crashes and auto-restart.

Maggy is gone (M1) so we no longer watch for it. We watch:
  - terry (the Discord bot — kept around for VC; restarted via start-terry.cmd)
  - gateway (the FastAPI hub — restarted via start-gateway.ps1)
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

from services.scout_daemon.config import SCRIPTS_DIR


@dataclass(frozen=True)
class ProcessStatus:
    name: str
    is_running: bool
    pid: int | None
    uptime_seconds: float | None


_ALLOWED_SEARCHES = frozenset({"terry.*bot", "-m gateway"})


def _find_process(search_term: str) -> tuple[int | None, float | None]:
    """Find a process by command-line substring. Returns (pid, uptime_s) or (None, None)."""
    if search_term not in _ALLOWED_SEARCHES:
        return None, None
    try:
        result = subprocess.run(
            [
                "powershell", "-Command",
                f"Get-CimInstance Win32_Process | "
                f"Where-Object {{ $_.CommandLine -match '{search_term}' }} | "
                f"Select-Object ProcessId, CreationDate | "
                f"ConvertTo-Json",
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None, None

        data = json.loads(result.stdout)
        if isinstance(data, list):
            data = data[0] if data else None
        if not data:
            return None, None

        pid = data.get("ProcessId")
        creation = data.get("CreationDate")
        uptime: float | None = None
        if creation and "/Date(" in str(creation):
            ts_ms = int(str(creation).split("(")[1].split(")")[0])
            uptime = time.time() - (ts_ms / 1000)
        return pid, uptime
    except Exception:
        return None, None


def check_terry() -> ProcessStatus:
    pid, uptime = _find_process("terry.*bot")
    return ProcessStatus(name="terry", is_running=pid is not None, pid=pid, uptime_seconds=uptime)


def check_gateway() -> ProcessStatus:
    pid, uptime = _find_process("-m gateway")
    return ProcessStatus(name="gateway", is_running=pid is not None, pid=pid, uptime_seconds=uptime)


def restart_terry() -> bool:
    try:
        subprocess.Popen(
            ["cmd.exe", "/C", str(SCRIPTS_DIR / "start-terry.cmd")],
            creationflags=0x00000008,
        )
        return True
    except Exception:
        return False


def restart_gateway() -> bool:
    try:
        subprocess.Popen(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(SCRIPTS_DIR / "start-gateway.ps1"),
            ],
            creationflags=0x00000008,
        )
        return True
    except Exception:
        return False
