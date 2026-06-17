"""GPU monitoring: temps, VRAM, game detection."""

from __future__ import annotations

import csv
import io
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class GPUStatus:
    index: int
    name: str
    temp_c: int
    vram_used_mb: int
    vram_total_mb: int
    utilization_pct: int

    @property
    def vram_free_mb(self) -> int:
        return self.vram_total_mb - self.vram_used_mb

    @property
    def vram_used_pct(self) -> float:
        if self.vram_total_mb == 0:
            return 0.0
        return (self.vram_used_mb / self.vram_total_mb) * 100


@dataclass(frozen=True)
class GPUProcess:
    pid: int
    gpu_index: int
    process_name: str


def query_gpu_status() -> list[GPUStatus]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,temperature.gpu,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []

        gpus: list[GPUStatus] = []
        reader = csv.reader(io.StringIO(result.stdout.strip()))
        for row in reader:
            if len(row) < 6:
                continue
            gpus.append(GPUStatus(
                index=int(row[0].strip()),
                name=row[1].strip(),
                temp_c=int(row[2].strip()),
                vram_used_mb=int(row[3].strip()),
                vram_total_mb=int(row[4].strip()),
                utilization_pct=int(row[5].strip()),
            ))
        return gpus
    except Exception:
        return []


def query_gpu_processes() -> list[GPUProcess]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,gpu_uuid,process_name",
                "--format=csv,noheader",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []

        uuid_result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,gpu_uuid", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        uuid_map: dict[str, int] = {}
        if uuid_result.returncode == 0:
            for line in uuid_result.stdout.strip().splitlines():
                parts = line.split(",", 1)
                if len(parts) == 2:
                    uuid_map[parts[1].strip()] = int(parts[0].strip())

        processes: list[GPUProcess] = []
        reader = csv.reader(io.StringIO(result.stdout.strip()))
        for row in reader:
            if len(row) < 3:
                continue
            pid = int(row[0].strip())
            gpu_uuid = row[1].strip()
            name = row[2].strip()
            gpu_idx = uuid_map.get(gpu_uuid, -1)
            processes.append(GPUProcess(pid=pid, gpu_index=gpu_idx, process_name=name))
        return processes
    except Exception:
        return []


_GAME_EXES = frozenset({
    "StarCitizen.exe", "javaw.exe", "Cyberpunk2077.exe", "HELLDIVERS2.exe",
    "GTA5.exe", "RDR2.exe", "bg3.exe", "eldenring.exe", "cs2.exe",
    "Palworld-Win64-Shipping.exe", "HogwartsLegacy.exe", "ForzaHorizon5.exe",
    "destiny2.exe", "Overwatch.exe", "VALORANT.exe", "RocketLeague.exe",
    "MonsterHunterWilds.exe",
})


def detect_game_on_gpu(gpu_index: int) -> str | None:
    for proc in query_gpu_processes():
        if proc.gpu_index == gpu_index:
            exe = proc.process_name.rsplit("\\", 1)[-1]
            if exe in _GAME_EXES:
                return exe
    return None
