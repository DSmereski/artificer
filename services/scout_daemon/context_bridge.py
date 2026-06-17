"""Shared system context that the M2 Sysmon helper reads via RPC."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field

from services.scout_daemon.config import CONTEXT_FILE


@dataclass
class SystemContext:
    last_updated: float = 0.0
    game_running: str | None = None
    game_gpu: int | None = None
    terry_online: bool = False
    terry_pid: int | None = None
    terry_uptime_s: float | None = None
    gateway_online: bool = False
    gateway_pid: int | None = None
    gateway_uptime_s: float | None = None
    daemon_online: bool = True
    gpu_temps: dict[int, int] = field(default_factory=dict)
    gpu_vram_used_pct: dict[int, float] = field(default_factory=dict)
    disk_free_gb: dict[str, float] = field(default_factory=dict)
    alerts: list[str] = field(default_factory=list)


def save_context(ctx: SystemContext) -> None:
    ctx_dict = asdict(ctx)
    ctx_dict["last_updated"] = time.time()
    ctx_dict["gpu_temps"] = {str(k): v for k, v in ctx_dict["gpu_temps"].items()}
    ctx_dict["gpu_vram_used_pct"] = {str(k): v for k, v in ctx_dict["gpu_vram_used_pct"].items()}

    CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONTEXT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(ctx_dict, indent=2), encoding="utf-8")
    tmp.replace(CONTEXT_FILE)


def load_context() -> SystemContext:
    if not CONTEXT_FILE.exists():
        return SystemContext()
    try:
        data = json.loads(CONTEXT_FILE.read_text(encoding="utf-8"))
        gpu_temps = {int(k): v for k, v in data.get("gpu_temps", {}).items()}
        gpu_vram = {int(k): v for k, v in data.get("gpu_vram_used_pct", {}).items()}
        return SystemContext(
            last_updated=data.get("last_updated", 0.0),
            game_running=data.get("game_running"),
            game_gpu=data.get("game_gpu"),
            terry_online=data.get("terry_online", False),
            terry_pid=data.get("terry_pid"),
            terry_uptime_s=data.get("terry_uptime_s"),
            gateway_online=data.get("gateway_online", False),
            gateway_pid=data.get("gateway_pid"),
            gateway_uptime_s=data.get("gateway_uptime_s"),
            daemon_online=data.get("daemon_online", True),
            gpu_temps=gpu_temps,
            gpu_vram_used_pct=gpu_vram,
            disk_free_gb=data.get("disk_free_gb", {}),
            alerts=data.get("alerts", []),
        )
    except (json.JSONDecodeError, KeyError):
        return SystemContext()
