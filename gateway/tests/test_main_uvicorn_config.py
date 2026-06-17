"""Verify uvicorn config builder relaxes WS keepalive timeouts.

Default uvicorn WS pings are interval=20s, timeout=20s — too aggressive
for mobile + Tailscale, leading to mid-turn disconnects when the phone
briefly backgrounds the app or hops networks. The gateway entrypoint
must override these to interval=30s, timeout=90s.
"""

from __future__ import annotations

from gateway.__main__ import _build_uvicorn_configs


def test_one_host_one_config() -> None:
    cfgs = _build_uvicorn_configs(app=object(), hosts=["127.0.0.1"], port=8766)
    assert len(cfgs) == 1
    assert cfgs[0].host == "127.0.0.1"
    assert cfgs[0].port == 8766


def test_multiple_hosts_share_port() -> None:
    cfgs = _build_uvicorn_configs(
        app=object(),
        hosts=["127.0.0.1", "0.0.0.0"],
        port=8766,
    )
    assert {c.host for c in cfgs} == {"127.0.0.1", "0.0.0.0"}
    assert all(c.port == 8766 for c in cfgs)


def test_ws_ping_interval_tolerates_phone_backgrounding() -> None:
    cfgs = _build_uvicorn_configs(app=object(), hosts=["127.0.0.1"], port=8766)
    cfg = cfgs[0]
    # Must be longer than uvicorn's 20s default — phone backgrounding
    # routinely freezes the WS handler for >20s.
    assert cfg.ws_ping_interval is not None
    assert cfg.ws_ping_interval >= 30.0


def test_ws_ping_timeout_allows_tailscale_hiccup() -> None:
    cfgs = _build_uvicorn_configs(app=object(), hosts=["127.0.0.1"], port=8766)
    cfg = cfgs[0]
    # Must be at least 60s — Tailscale phone-side reconnect takes up to
    # ~30s on flaky networks, plus some buffer.
    assert cfg.ws_ping_timeout is not None
    assert cfg.ws_ping_timeout >= 60.0


def test_lifespan_off_so_outer_runs_it_once() -> None:
    """Lifespan must be 'off' on the per-host configs because
    `_serve_many` enters `app.router.lifespan_context` itself once
    for all hosts. Letting uvicorn run the lifespan would double-fire
    startup/shutdown (e.g. spawn duplicate background tasks)."""
    cfgs = _build_uvicorn_configs(app=object(), hosts=["127.0.0.1"], port=8766)
    assert cfgs[0].lifespan == "off"
