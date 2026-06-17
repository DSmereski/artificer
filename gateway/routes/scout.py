"""Scout dashboard routes.

GET /v1/scout/status   — current snapshot (GPUs, disks, bot heartbeats)
GET /v1/scout/history  — rolling samples for graphs
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from gateway.deps import require_device, require_device_or_loopback, state


router = APIRouter(prefix="/v1/scout", tags=["scout"])
log = logging.getLogger("gateway.scout")


class GPUInfo(BaseModel):
    index: int
    name: str
    temp_c: int
    vram_used_mb: int
    vram_total_mb: int
    vram_used_pct: float
    utilization_pct: int
    game: str | None = None


class DiskInfo(BaseModel):
    drive: str
    free_gb: float
    total_gb: float
    used_pct: float


class BotHeartbeat(BaseModel):
    name: str
    is_running: bool
    pid: int | None
    uptime_seconds: float | None


class CpuInfo(BaseModel):
    usage_pct: float
    cores_logical: int
    cores_physical: int | None = None
    freq_mhz: float | None = None


class RamInfo(BaseModel):
    used_gb: float
    total_gb: float
    used_pct: float


class HostInfo(BaseModel):
    cpu: CpuInfo | None = None
    ram: RamInfo | None = None
    uptime_seconds: float = 0.0


class ScoutStatus(BaseModel):
    gpus: list[GPUInfo]
    disks: list[DiskInfo]
    bots: list[BotHeartbeat]
    host: HostInfo | None = None


def _snapshot() -> ScoutStatus:
    """Read the current snapshot. Isolated so tests can monkeypatch.

    M1: sources moved from `bots.scout` (deleted) to
    `services.scout_daemon`. Maggy is gone, so we only heartbeat Terry
    + the gateway.
    """
    from services.scout_daemon.gpu_monitor import detect_game_on_gpu, query_gpu_status
    from services.scout_daemon.system_monitor import check_all_disks, check_host
    from services.scout_daemon.watchdog import check_gateway, check_terry

    gpu_rows: list[GPUInfo] = []
    for g in query_gpu_status():
        game = None
        try:
            game = detect_game_on_gpu(g.index)
        except Exception:  # noqa: BLE001
            game = None
        gpu_rows.append(GPUInfo(
            index=g.index,
            name=g.name,
            temp_c=g.temp_c,
            vram_used_mb=g.vram_used_mb,
            vram_total_mb=g.vram_total_mb,
            vram_used_pct=round(g.vram_used_pct, 1),
            utilization_pct=g.utilization_pct,
            game=game,
        ))

    disks = [
        DiskInfo(**asdict(d)) for d in check_all_disks()
    ]

    bot_rows: list[BotHeartbeat] = []
    for checker in (check_terry, check_gateway):
        try:
            bs = checker()
            bot_rows.append(BotHeartbeat(
                name=bs.name, is_running=bs.is_running,
                pid=bs.pid, uptime_seconds=bs.uptime_seconds,
            ))
        except Exception as e:  # noqa: BLE001
            log.warning("scout heartbeat failed for %s: %s", checker.__name__, e)

    host_status = check_host()
    host_info = HostInfo(
        cpu=CpuInfo(**asdict(host_status.cpu)) if host_status.cpu else None,
        ram=RamInfo(**asdict(host_status.ram)) if host_status.ram else None,
        uptime_seconds=host_status.uptime_seconds,
    )

    return ScoutStatus(
        gpus=gpu_rows, disks=disks, bots=bot_rows, host=host_info,
    )


@router.get("/status", response_model=ScoutStatus)
def status_now(
    device=Depends(require_device_or_loopback),
    request: Request = None,
) -> ScoutStatus:
    """Return the live snapshot. Clients poll every few seconds."""
    snap = _snapshot()
    st = state(request)
    if st.scout_history is not None:
        try:
            st.scout_history.append(snap.model_dump())
        except Exception as e:  # noqa: BLE001
            log.warning("scout history append failed: %s", e)
    return snap


@router.get("/history")
def history(
    since: float | None = Query(default=None, ge=0),
    limit: int | None = Query(default=500, ge=1, le=5000),
    device=Depends(require_device_or_loopback),
    request: Request = None,
) -> list[dict]:
    st = state(request)
    hist = st.scout_history
    if hist is None:
        return []
    return hist.read(since=since, limit=limit)
