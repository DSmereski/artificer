"""Scout-side proactive Hive trigger.

When PROACTIVE_HIVE_ENABLED=true in config/.env, the scout daemon calls
`maybe_trigger` after a new alert is appended to context.alerts.  The
function POSTs to the gateway's /v1/proactive/trigger endpoint (best
effort — any failure is logged and swallowed so the scout never crashes).

Guard: tracks the last trigger time per reason so the same condition
can't flood the gateway.  Minimum interval between triggers of the
same reason: TRIGGER_INTERVAL_S (default 300s / 5 min).
"""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
import json

log = logging.getLogger("scout_daemon.proactive_hive")

# Minimum seconds between two proactive triggers with the same reason.
TRIGGER_INTERVAL_S: float = 300.0

# Per-reason last-trigger timestamps.
_last_trigger: dict[str, float] = {}


def maybe_trigger(
    reason: str,
    context: str = "",
    *,
    gateway_url: str,
    auth_token: str,
    audience: str = "owner",
) -> None:
    """POST a proactive trigger to the gateway, best-effort, rate-limited.

    This is intentionally synchronous (called from a daemon worker thread)
    and blocks at most 10 s on the HTTP call.  Any failure is logged and
    swallowed — the scout daemon must never crash due to a proactive call.
    """
    now = time.monotonic()
    last = _last_trigger.get(reason, 0.0)
    if now - last < TRIGGER_INTERVAL_S:
        log.debug(
            "proactive trigger rate-limited: reason=%r (%.0fs ago)",
            reason[:60], now - last,
        )
        return

    _last_trigger[reason] = now

    url = f"{gateway_url.rstrip('/')}/v1/proactive/trigger"
    payload = json.dumps({
        "reason": reason[:500],
        "context": context[:2000],
        "audience": audience,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {auth_token}",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            body = resp.read(512).decode("utf-8", errors="replace")
            if status >= 400:
                log.warning(
                    "proactive trigger rejected (%d): %s", status, body[:200],
                )
            else:
                log.info("proactive trigger accepted (%d): reason=%r", status, reason[:60])
    except (urllib.error.URLError, OSError, Exception) as e:  # noqa: BLE001
        log.debug("proactive trigger failed (best-effort): %s", e)
