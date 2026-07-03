"""Tests for surfacing per-GPU compute processes to the scout status payload.

Covers gpu_monitor helpers (friendly names, grouping/sorting) and the
scout._snapshot wiring that attaches processes to each GPUInfo card.
"""
from __future__ import annotations

import pytest

from services.scout_daemon import gpu_monitor
from services.scout_daemon.gpu_monitor import (
    GPUProcess,
    GPUStatus,
    friendly_process_name,
    processes_by_gpu,
)


# ── friendly_process_name ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("C:\\Program Files\\Ollama\\ollama.exe", "ollama"),
    ("ollama.exe", "ollama"),
    ("python.exe", "python"),
    ("/usr/bin/python3", "python3"),
    ("C:\\Program Files\\ExampleGame\\ExampleGame.exe", "ExampleGame"),
    ("weird-no-ext", "weird-no-ext"),
])
def test_friendly_process_name(raw: str, expected: str) -> None:
    assert friendly_process_name(raw) == expected


# ── processes_by_gpu: grouping + heaviest-first sort ──────────────────────────

def test_processes_by_gpu_groups_and_sorts() -> None:
    procs = [
        GPUProcess(pid=1, gpu_index=0, process_name="python.exe", used_memory_mb=512),
        GPUProcess(pid=2, gpu_index=0, process_name="ollama.exe", used_memory_mb=18000),
        GPUProcess(pid=3, gpu_index=1, process_name="ollama.exe", used_memory_mb=4000),
    ]
    grouped = processes_by_gpu(procs)

    assert set(grouped.keys()) == {0, 1}
    # GPU 0 sorted heaviest-first: ollama (18000) before python (512)
    assert [p.pid for p in grouped[0]] == [2, 1]
    assert grouped[0][0].used_memory_mb == 18000
    # GPU 1 has the single ollama process
    assert [p.pid for p in grouped[1]] == [3]


def test_processes_by_gpu_empty() -> None:
    assert processes_by_gpu([]) == {}


# ── _snapshot attaches processes to each card ─────────────────────────────────

def test_snapshot_attaches_processes(monkeypatch) -> None:
    from gateway.routes import scout as scout_route

    fake_gpus = [
        GPUStatus(
            index=0, name="NVIDIA GeForce RTX 5060 Ti", temp_c=55,
            vram_used_mb=18000, vram_total_mb=16384, utilization_pct=80,
        ),
        GPUStatus(
            index=1, name="NVIDIA GeForce RTX 5060 Ti", temp_c=40,
            vram_used_mb=0, vram_total_mb=16384, utilization_pct=0,
        ),
    ]
    fake_procs = {
        0: [
            GPUProcess(pid=2, gpu_index=0, process_name="C:\\Program Files\\Ollama\\ollama.exe", used_memory_mb=17000),
            GPUProcess(pid=1, gpu_index=0, process_name="python.exe", used_memory_mb=900),
        ],
        # GPU 1 has no processes → idle
    }

    monkeypatch.setattr(gpu_monitor, "query_gpu_status", lambda: fake_gpus)
    monkeypatch.setattr(gpu_monitor, "processes_by_gpu", lambda: fake_procs)
    # Disk/host/watchdog are unrelated — stub to avoid host calls.
    monkeypatch.setattr(
        "services.scout_daemon.system_monitor.check_all_disks", lambda: []
    )

    class _Host:
        cpu = None
        ram = None
        uptime_seconds = 0.0

    monkeypatch.setattr(
        "services.scout_daemon.system_monitor.check_host", lambda: _Host()
    )

    class _Bot:
        name = "hive"
        is_running = True
        pid = 1
        uptime_seconds = 1.0

    monkeypatch.setattr("services.scout_daemon.watchdog.check_hive", lambda: _Bot())
    monkeypatch.setattr("services.scout_daemon.watchdog.check_gateway", lambda: _Bot())

    status = scout_route._snapshot()

    by_index = {g.index: g for g in status.gpus}

    # GPU 0: two processes, ollama friendly-named + heaviest first
    g0 = by_index[0]
    assert len(g0.processes) == 2
    assert g0.processes[0].name == "ollama"
    assert g0.processes[0].used_memory_mb == 17000
    assert g0.processes[1].name == "python"
    # pids preserved
    assert g0.processes[0].pid == 2

    # GPU 1: idle (no processes)
    g1 = by_index[1]
    assert g1.processes == []


def test_snapshot_detects_game_from_processes(monkeypatch) -> None:
    """A known game exe in the process list still surfaces as the game tag."""
    from gateway.routes import scout as scout_route

    fake_gpus = [
        GPUStatus(
            index=2, name="NVIDIA GeForce RTX 4080", temp_c=70,
            vram_used_mb=8000, vram_total_mb=16384, utilization_pct=95,
        ),
    ]
    fake_procs = {
        2: [
            GPUProcess(pid=99, gpu_index=2, process_name="C:\\Program Files\\StarCitizen\\StarCitizen.exe", used_memory_mb=8000),
        ],
    }

    monkeypatch.setattr(gpu_monitor, "query_gpu_status", lambda: fake_gpus)
    monkeypatch.setattr(gpu_monitor, "processes_by_gpu", lambda: fake_procs)
    monkeypatch.setattr("services.scout_daemon.system_monitor.check_all_disks", lambda: [])

    class _Host:
        cpu = None
        ram = None
        uptime_seconds = 0.0

    monkeypatch.setattr("services.scout_daemon.system_monitor.check_host", lambda: _Host())

    class _Bot:
        name = "hive"
        is_running = True
        pid = 1
        uptime_seconds = 1.0

    monkeypatch.setattr("services.scout_daemon.watchdog.check_hive", lambda: _Bot())
    monkeypatch.setattr("services.scout_daemon.watchdog.check_gateway", lambda: _Bot())

    status = scout_route._snapshot()
    g = status.gpus[0]
    assert g.game == "StarCitizen.exe"
    assert g.processes[0].name == "StarCitizen"
