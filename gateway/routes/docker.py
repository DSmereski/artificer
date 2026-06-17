"""Docker status route.

GET /v1/docker/status — list local Docker containers (name, state, status,
image, health) by shelling out to `docker ps`. Loopback-exempt like the other
read endpoints (the wallpaper dashboard polls it). Degrades gracefully when
Docker is not installed or the daemon is down (returns available=false).
"""

from __future__ import annotations

import json
import logging
import subprocess

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from gateway.deps import require_device_or_loopback

log = logging.getLogger("gateway.docker")

router = APIRouter(prefix="/v1/docker", tags=["docker"])

# `docker ps` can hang if the daemon is wedged; cap it hard.
_DOCKER_TIMEOUT_S = 4.0


class DockerContainer(BaseModel):
    name: str
    state: str = ""        # running | exited | paused | created | ...
    status: str = ""       # human string, e.g. "Up 43 minutes"
    image: str = ""
    health: str = ""       # healthy | unhealthy | starting | "" (no healthcheck)


class DockerStatus(BaseModel):
    available: bool
    reason: str = ""
    running: int = 0
    total: int = 0
    containers: list[DockerContainer] = []


def _parse_health(status: str) -> str:
    s = status.lower()
    if "(healthy)" in s:
        return "healthy"
    if "(unhealthy)" in s:
        return "unhealthy"
    if "health: starting" in s or "(starting)" in s:
        return "starting"
    return ""


def _snapshot() -> DockerStatus:
    try:
        proc = subprocess.run(
            ["docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=_DOCKER_TIMEOUT_S, check=False,
        )
    except FileNotFoundError:
        return DockerStatus(available=False, reason="docker not installed")
    except subprocess.TimeoutExpired:
        return DockerStatus(available=False, reason="docker timed out")
    except OSError as e:  # noqa: BLE001
        return DockerStatus(available=False, reason=f"docker error: {e}")

    if proc.returncode != 0:
        # Daemon down / not reachable.
        err = (proc.stderr or "").strip().splitlines()
        return DockerStatus(
            available=False,
            reason=err[-1][:160] if err else "docker daemon unreachable",
        )

    containers: list[DockerContainer] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        status = str(row.get("Status", ""))
        containers.append(DockerContainer(
            name=str(row.get("Names", "")),
            state=str(row.get("State", "")).lower(),
            status=status,
            image=str(row.get("Image", "")),
            health=_parse_health(status),
        ))

    running = sum(1 for c in containers if c.state == "running")
    return DockerStatus(
        available=True,
        running=running,
        total=len(containers),
        containers=containers,
    )


@router.get("/status", response_model=DockerStatus)
def docker_status(
    device=Depends(require_device_or_loopback),
) -> DockerStatus:
    """Local Docker container snapshot. Polled by the dashboard."""
    return _snapshot()
