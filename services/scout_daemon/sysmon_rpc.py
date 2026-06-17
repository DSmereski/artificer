"""Localhost-only HTTP RPC for the M2 Sysmon helper.

Uses the stdlib http.server (no extra deps). Bound to 127.0.0.1 only —
not Tailscale, not 0.0.0.0. The Sysmon helper inside the gateway
process makes a localhost GET to fetch the latest snapshot.

Endpoints:
  GET /sysmon/snapshot  -> full SystemContext as JSON
  GET /sysmon/gpu       -> just the GPU array
  GET /sysmon/disk      -> just the disk array
  GET /sysmon/health    -> {"ok": true} for liveness probes
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from services.scout_daemon.config import RPC_HOST, RPC_PORT
from services.scout_daemon.context_bridge import SystemContext, load_context

log = logging.getLogger("scout_daemon.rpc")


def _serialise(ctx: SystemContext) -> dict:
    d = asdict(ctx)
    d["gpu_temps"] = {str(k): v for k, v in d["gpu_temps"].items()}
    d["gpu_vram_used_pct"] = {str(k): v for k, v in d["gpu_vram_used_pct"].items()}
    return d


class _Handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (stdlib name)
        # Defensive: only respond to localhost. ThreadingHTTPServer is
        # bound to 127.0.0.1 below, but double-check on each request.
        client_ip = self.client_address[0]
        if client_ip not in ("127.0.0.1", "::1"):
            self._send_json(403, {"error": "localhost only"})
            return

        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/sysmon/health":
            self._send_json(200, {"ok": True})
            return
        if path == "/sysmon/snapshot":
            self._send_json(200, _serialise(load_context()))
            return
        if path == "/sysmon/gpu":
            ctx = load_context()
            payload = {
                "temps": {str(k): v for k, v in ctx.gpu_temps.items()},
                "vram_used_pct": {str(k): v for k, v in ctx.gpu_vram_used_pct.items()},
                "game_running": ctx.game_running,
                "game_gpu": ctx.game_gpu,
            }
            self._send_json(200, payload)
            return
        if path == "/sysmon/disk":
            self._send_json(200, load_context().disk_free_gb)
            return
        self._send_json(404, {"error": "not found", "path": path})

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # Quieter than the default stderr spam.
        log.debug("rpc %s", format % args)


def serve_forever() -> None:
    server = ThreadingHTTPServer((RPC_HOST, RPC_PORT), _Handler)
    log.info("scout-daemon RPC listening on http://%s:%d", RPC_HOST, RPC_PORT)
    server.serve_forever()


def start_in_background() -> threading.Thread:
    t = threading.Thread(target=serve_forever, daemon=True, name="scout-rpc")
    t.start()
    return t
