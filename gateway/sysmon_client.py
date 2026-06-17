"""Localhost-only client for the scout-daemon's sysmon RPC (M6.2).

The scout daemon (M1) exposes 127.0.0.1:8767 with snapshot endpoints.
This client wraps them so the M6.2 Sysmon helper can fetch state
without needing to know about the daemon's HTTP shape.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("gateway.sysmon_client")

_BASE = "http://127.0.0.1:8767"
_TIMEOUT_S = 3.0


async def fetch_snapshot() -> dict[str, Any] | None:
    """Returns the SystemContext as a dict, or None if the daemon is
    unreachable. Bound to localhost only — no SSRF surface."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as http:
            r = await http.get(f"{_BASE}/sysmon/snapshot")
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        log.info("sysmon RPC unreachable: %s", e)
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("sysmon RPC unexpected error: %s", e)
        return None
