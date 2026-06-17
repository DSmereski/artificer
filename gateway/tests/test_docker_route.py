"""Tests for the /v1/docker/status snapshot parsing + graceful degradation."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from gateway.routes import docker as dk


def _fake_proc(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["docker"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


def test_parses_running_and_exited():
    out = "\n".join([
        '{"Names":"or9-pg","State":"running","Status":"Up 43 minutes","Image":"postgres:16"}',
        '{"Names":"ntfy","State":"exited","Status":"Exited (255) 2 weeks ago","Image":"binwiederhier/ntfy"}',
    ])
    with patch("subprocess.run", return_value=_fake_proc(out)):
        s = dk._snapshot()
    assert s.available is True
    assert s.total == 2
    assert s.running == 1
    assert s.containers[0].name == "or9-pg"
    assert s.containers[0].state == "running"


def test_parses_health():
    out = '{"Names":"db","State":"running","Status":"Up 2 hours (healthy)","Image":"x"}'
    with patch("subprocess.run", return_value=_fake_proc(out)):
        s = dk._snapshot()
    assert s.containers[0].health == "healthy"


def test_docker_not_installed_degrades():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        s = dk._snapshot()
    assert s.available is False
    assert "not installed" in s.reason


def test_daemon_down_degrades():
    with patch("subprocess.run", return_value=_fake_proc("", returncode=1, stderr="Cannot connect to the Docker daemon")):
        s = dk._snapshot()
    assert s.available is False
    assert s.total == 0


def test_timeout_degrades():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 4)):
        s = dk._snapshot()
    assert s.available is False
    assert "timed out" in s.reason
