"""Entrypoint: `python -m gateway`.

Supports binding both loopback (for the PC) and the Tailscale interface
(for the phone) at the same time. Runs one uvicorn Server per host, sharing
a single FastAPI app and a single lifespan run.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import uvicorn

from gateway.app import create_app
from gateway.config import load_config

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# WebSocket keepalive — tuned for mobile + Tailscale.
#
# Defaults (interval=20s, timeout=20s) are too aggressive for a phone
# that may briefly background the app or hop networks. A missed pong
# under those defaults severs the chat WS mid-turn and the user sees
# a "connection lost" toast even though the server is still computing
# the reply. Extended values tolerate ~2 min of client unresponsiveness
# before declaring the connection dead — still well within the
# `hive_turn_total_s=150` budget for detecting genuinely dead clients.
_WS_PING_INTERVAL_S = 30.0
_WS_PING_TIMEOUT_S = 90.0


def _build_uvicorn_configs(app, hosts: list[str], port: int) -> list[uvicorn.Config]:
    """Build one uvicorn Config per host. Extracted for unit testing."""
    return [
        uvicorn.Config(
            app=app,
            host=h,
            port=port,
            log_level="info",
            access_log=False,
            lifespan="off",  # caller runs the lifespan once for the whole app
            ws_ping_interval=_WS_PING_INTERVAL_S,
            ws_ping_timeout=_WS_PING_TIMEOUT_S,
        )
        for h in hosts
    ]


# How long to wait between Tailscale-bind retries. The Tailscale interface
# often isn't up yet when the gateway starts at logon (the boot task races
# tailscaled coming online), so the 100.x bind fails and — without a retry —
# the gateway runs loopback-only until a manual restart. The phone (which
# reaches the PC only over Tailscale) then can't connect. Retrying in the
# background binds the 100.x address as soon as Tailscale is up.
_REBIND_RETRY_S = 30.0


async def _serve_one(
    make_server, host: str, *, retry_bind: bool = False,
) -> None:
    """Serve on one host; log + swallow a bind failure so it can't take the
    other interfaces down. A wedged/absent Tailscale interface must NOT kill
    the loopback server the local dashboard depends on.

    When ``retry_bind`` is set (the Tailscale interface), keep retrying the
    bind on a fixed interval until it succeeds, so a Tailscale interface that
    comes up *after* gateway startup still gets bound without a manual
    restart. CancelledError (BaseException, not Exception) propagates so a
    real shutdown stops the loop cleanly.
    """
    log = logging.getLogger("gateway")
    while True:
        server = make_server()
        try:
            await server.serve()
            return  # clean shutdown of a server that did bind
        except (Exception, SystemExit) as e:  # noqa: BLE001
            # uvicorn raises SystemExit (NOT an Exception) on a failed bind —
            # catch it explicitly, else it propagates past gather() and tears
            # down the loopback server too. Isolate the failure to this host.
            if not retry_bind:
                log.error(
                    "bind/serve failed for host %s: %s "
                    "(other interfaces continue)", host, e,
                )
                return
            log.warning(
                "bind/serve failed for host %s: %s — retrying in %.0fs "
                "(Tailscale may still be coming up)", host, e, _REBIND_RETRY_S,
            )
            await asyncio.sleep(_REBIND_RETRY_S)


async def _serve_many(app, hosts: list[str], port: int) -> None:
    # Run the FastAPI lifespan ourselves so adapters/canon/tasks are set up
    # exactly once no matter how many interfaces we listen on.
    async with app.router.lifespan_context(app):
        configs = _build_uvicorn_configs(app, hosts, port)
        # The first host is the primary loopback bind (must-succeed, no retry);
        # any additional host is the Tailscale interface, which we retry-bind
        # so it survives Tailscale coming up late.
        await asyncio.gather(
            *(
                _serve_one(
                    (lambda c=c: uvicorn.Server(c)), h,
                    retry_bind=(i > 0),
                )
                for i, (c, h) in enumerate(zip(configs, hosts))
            ),
            return_exceptions=True,
        )


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("GATEWAY_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg_path = Path(os.environ.get(
        "GATEWAY_CONFIG",
        str(_PROJECT_ROOT / "config" / "gateway.yaml"),
    ))
    cfg = load_config(cfg_path)
    app = create_app(cfg)

    hosts: list[str] = [cfg.bind_host]
    if cfg.tailscale_bind and cfg.tailscale_bind != cfg.bind_host:
        hosts.append(cfg.tailscale_bind)

    asyncio.run(_serve_many(app, hosts, cfg.bind_port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
