"""Hardware + runtime probe — emits the Phase 1 capability snapshot.

Phase 1 reports what's *detected* but does not install or configure
runtimes; the `runtimes` block carries `{installed: false}` placeholders
so the host knows what's missing.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from typing import Any

from hive_node_agent.version import __version__


log = logging.getLogger("hive_node_agent.probe")


_NVIDIA_SMI_QUERY = (
    "index,name,memory.total,memory.free,driver_version,"
    "compute_cap"  # used as a CUDA-version proxy fallback
)


def _run_nvidia_smi() -> str | None:
    """Return CSV output of nvidia-smi, or None if not available."""
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.free,driver_version,cuda_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def parse_nvidia_smi_csv(raw: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        try:
            rows.append({
                "index": int(parts[0]),
                "name": parts[1],
                "vram_total_mb": int(parts[2]),
                "vram_free_mb": int(parts[3]),
                "driver": parts[4],
                "cuda": parts[5],
            })
        except ValueError:
            continue
    return rows


def _ram_gb() -> tuple[float, float]:
    """Return (total_gb, free_gb). Cross-platform best-effort."""
    try:
        import psutil  # optional dependency
        m = psutil.virtual_memory()
        return round(m.total / (1024**3), 1), round(m.available / (1024**3), 1)
    except ImportError:
        log.debug("psutil not installed; falling back to /proc/meminfo")
    except Exception as exc:
        log.warning("psutil RAM probe failed: %s", exc)
    # Linux /proc/meminfo fallback.
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            kv = {}
            for line in f:
                k, _, v = line.partition(":")
                kv[k.strip()] = v.strip()
        total_kb = int(kv["MemTotal"].split()[0])
        free_kb = int(kv.get("MemAvailable", kv.get("MemFree", "0 kB")).split()[0])
        return round(total_kb / (1024**2), 1), round(free_kb / (1024**2), 1)
    except (OSError, KeyError, ValueError) as exc:
        log.debug("/proc/meminfo RAM probe failed: %s", exc)
        return 0.0, 0.0


def _disk_free_gb() -> float:
    try:
        usage = shutil.disk_usage("/" if platform.system() != "Windows" else "C:\\")
        return round(usage.free / (1024**3), 1)
    except OSError:
        return 0.0


def _runtime_stub(*, installed: bool = False) -> dict[str, Any]:
    """Phase 2-shaped runtime entry. Phase 1 only fills `installed`;
    the other fields are present so downstream consumers (host
    scheduler, admin UI) don't have to special-case the v1 schema.

    Schema:
      installed:   bool  — runtime detected on PATH
      version:     str   — semver / build tag, "" until Phase 3 probes
      endpoint:    str   — local URL the host can dispatch to, "" until wired
      models:      list[str] — locally available models, [] until Phase 3
      concurrency: int   — max in-flight jobs the runtime can serve
      health:      str   — "unknown" | "ok" | "degraded" | "down"
    """
    return {
        "installed": installed,
        "version": "",
        "endpoint": "",
        "models": [],
        "concurrency": 0,
        "health": "unknown",
    }


def _detect_runtimes() -> dict[str, dict[str, Any]]:
    """Phase 1: presence-detect only. Phase 3 fills in version + models +
    health probes per runtime.
    """
    return {
        "ollama": _runtime_stub(installed=shutil.which("ollama") is not None),
        "comfy":  _runtime_stub(),
        "embed":  _runtime_stub(),
        "i2v":    _runtime_stub(),
    }


def collect(labels: tuple[str, ...] = ()) -> dict[str, Any]:
    raw = _run_nvidia_smi()
    gpus = parse_nvidia_smi_csv(raw) if raw else []
    ram_total, ram_free = _ram_gb()
    return {
        "agent_version": __version__,
        "os": {
            "family": platform.system().lower(),
            "version": platform.release(),
            "build": platform.version(),
        },
        "cpu": {
            "model": platform.processor() or platform.machine(),
            "cores": _cpu_count(logical=False),
            "threads": _cpu_count(logical=True),
        },
        "ram_total_gb": ram_total,
        "ram_free_gb": ram_free,
        "gpus": gpus,
        "disk_free_gb": _disk_free_gb(),
        "runtimes": _detect_runtimes(),
        "labels": list(labels),
    }


def _cpu_count(*, logical: bool) -> int:
    try:
        import psutil
        return psutil.cpu_count(logical=logical) or 0
    except ImportError:
        log.debug("psutil not installed; using os.cpu_count()")
    except Exception as exc:
        log.warning("psutil CPU probe failed: %s", exc)
    import os
    return os.cpu_count() or 0
