"""Smoke tests for the localhost-only sysmon RPC server."""

from __future__ import annotations

import json
import time
import urllib.request
from http.server import ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch

import pytest

from services.scout_daemon import context_bridge, sysmon_rpc


@pytest.fixture
def rpc_server(tmp_path, monkeypatch):
    """Spin a one-off RPC server bound to a free localhost port."""
    # Redirect the context file into tmp_path so tests don't stomp on real state.
    ctx_file = tmp_path / "scout-context.json"
    monkeypatch.setattr("services.scout_daemon.config.CONTEXT_FILE", ctx_file)
    monkeypatch.setattr("services.scout_daemon.context_bridge.CONTEXT_FILE", ctx_file)

    ctx = context_bridge.SystemContext(
        gpu_temps={0: 65, 1: 72},
        gpu_vram_used_pct={0: 45.0, 1: 88.0},
        disk_free_gb={"C:\\": 120.5, "D:\\": 8000.0},
        game_running=None,
        terry_online=True,
        gateway_online=True,
    )
    context_bridge.save_context(ctx)

    server = ThreadingHTTPServer(("127.0.0.1", 0), sysmon_rpc._Handler)
    port = server.server_port
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=2) as resp:
        return json.loads(resp.read())


def test_health(rpc_server):
    j = _get(f"{rpc_server}/sysmon/health")
    assert j == {"ok": True}


def test_snapshot(rpc_server):
    j = _get(f"{rpc_server}/sysmon/snapshot")
    assert j["terry_online"] is True
    assert j["gateway_online"] is True
    assert j["gpu_temps"]["0"] == 65
    assert j["gpu_temps"]["1"] == 72


def test_gpu(rpc_server):
    j = _get(f"{rpc_server}/sysmon/gpu")
    assert j["temps"]["0"] == 65
    assert j["vram_used_pct"]["1"] == 88.0
    assert j["game_running"] is None


def test_disk(rpc_server):
    j = _get(f"{rpc_server}/sysmon/disk")
    assert j["C:\\"] == 120.5


def test_unknown_path_404(rpc_server):
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _get(f"{rpc_server}/sysmon/nope")
    assert exc_info.value.code == 404
