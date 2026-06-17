"""System monitoring: disk space, CPU, RAM."""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiskStatus:
    drive: str
    free_gb: float
    total_gb: float
    used_pct: float


@dataclass(frozen=True)
class CpuStatus:
    usage_pct: float            # rolling 1s sample
    cores_logical: int
    cores_physical: int | None
    freq_mhz: float | None      # current frequency, or None if unavailable


@dataclass(frozen=True)
class RamStatus:
    used_gb: float
    total_gb: float
    used_pct: float


@dataclass(frozen=True)
class HostStatus:
    """System-wide non-disk readings — populated when psutil is available."""
    cpu: CpuStatus | None
    ram: RamStatus | None
    uptime_seconds: float


def check_disk(path: str = "C:\\") -> DiskStatus:
    usage = shutil.disk_usage(path)
    free_gb = usage.free / (1024 ** 3)
    total_gb = usage.total / (1024 ** 3)
    used_pct = (usage.used / usage.total) * 100
    return DiskStatus(
        drive=path,
        free_gb=round(free_gb, 1),
        total_gb=round(total_gb, 1),
        used_pct=round(used_pct, 1),
    )


def check_all_disks() -> list[DiskStatus]:
    disks: list[DiskStatus] = []
    for letter in "CDEF":
        path = f"{letter}:\\"
        if Path(path).exists():
            disks.append(check_disk(path))
    return disks


# Module-global so consecutive cpu_percent() reads sample over an
# interval rather than always returning 0.0 on the first call.
_cpu_warmup_done = False


def check_cpu() -> CpuStatus | None:
    """Live CPU readings. Returns None if psutil isn't installed."""
    global _cpu_warmup_done
    try:
        import psutil  # type: ignore
    except ImportError:
        return None
    # First call to cpu_percent() returns 0.0 — warm up once with a
    # short interval, then non-blocking afterwards.
    if not _cpu_warmup_done:
        psutil.cpu_percent(interval=0.1)
        _cpu_warmup_done = True
    pct = float(psutil.cpu_percent(interval=None))
    freq_mhz: float | None = None
    try:
        f = psutil.cpu_freq()
        if f is not None and f.current:
            freq_mhz = round(float(f.current), 1)
    except (NotImplementedError, OSError):
        pass
    return CpuStatus(
        usage_pct=round(pct, 1),
        cores_logical=psutil.cpu_count(logical=True) or 0,
        cores_physical=psutil.cpu_count(logical=False),
        freq_mhz=freq_mhz,
    )


def check_ram() -> RamStatus | None:
    """Live RAM readings. Returns None if psutil isn't installed."""
    try:
        import psutil  # type: ignore
    except ImportError:
        return None
    m = psutil.virtual_memory()
    return RamStatus(
        used_gb=round(m.used / (1024 ** 3), 1),
        total_gb=round(m.total / (1024 ** 3), 1),
        used_pct=round(float(m.percent), 1),
    )


def check_host() -> HostStatus:
    """System-wide rollup. Always returns a value — uptime works
    without psutil; cpu/ram are None when psutil isn't installed."""
    try:
        import psutil  # type: ignore
        uptime = time.time() - psutil.boot_time()
    except ImportError:
        uptime = 0.0
    return HostStatus(
        cpu=check_cpu(),
        ram=check_ram(),
        uptime_seconds=round(uptime, 1),
    )
