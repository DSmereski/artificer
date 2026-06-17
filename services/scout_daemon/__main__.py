"""Scout daemon entry point.

Run as: `python -m services.scout_daemon`

Loops:
  - Watchdog: every WATCHDOG_INTERVAL seconds, check Terry + gateway,
    auto-restart if either is down.
  - GPU monitor: every GPU_CHECK_INTERVAL seconds, snapshot temps/VRAM
    + detect a game on the gaming GPU.
  - System monitor: every HEALTH_CHECK_INTERVAL seconds, snapshot
    disk space.
  - All snapshots persisted to scout-context.json.
  - RPC server runs in a daemon thread on 127.0.0.1:8767.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import urllib.error
import urllib.request
from logging.handlers import RotatingFileHandler

from services.scout_daemon import context_bridge, gpu_monitor, gateway_supervisor, system_monitor, sysmon_rpc, watchdog
from services.scout_daemon.config import (
    DAEMON_LOG, DISK_WARN_GB, GAMING_GPU, GATEWAY_AUTORESTART, GATEWAY_AUTH_TOKEN,
    GATEWAY_URL, GPU_CHECK_INTERVAL, GPU_TEMP_CRITICAL, GPU_TEMP_WARN,
    HEALTH_CHECK_INTERVAL, LOG_DIR, NTFY_TOPIC, NTFY_URL,
    PROACTIVE_HIVE_ENABLED, WATCHDOG_INTERVAL,
)


def _ntfy_push(title: str, message: str, priority: str = "high") -> None:
    """Best-effort ntfy push. Swallows all errors — never crashes the daemon."""
    try:
        url = f"{NTFY_URL.rstrip('/')}/{NTFY_TOPIC}"
        body = message.encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "Title": title,
                "Priority": priority,
                "Content-Type": "text/plain; charset=utf-8",
            },
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except (urllib.error.URLError, OSError, Exception):  # noqa: BLE001
        pass


def _setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(DAEMON_LOG, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # Also echo to stdout for foreground runs.
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    root.addHandler(stream)
    return logging.getLogger("scout_daemon")


def _watchdog_loop(log: logging.Logger, ctx: context_bridge.SystemContext) -> None:
    while True:
        try:
            t = watchdog.check_terry()
            ctx.terry_online = t.is_running
            ctx.terry_pid = t.pid
            ctx.terry_uptime_s = t.uptime_seconds
            if not t.is_running:
                log.warning("terry not running — restarting")
                if watchdog.restart_terry():
                    ctx.alerts.append(f"restarted terry @ {time.strftime('%H:%M:%S')}")
                    ctx.alerts = ctx.alerts[-100:]

            g = watchdog.check_gateway()
            ctx.gateway_online = g.is_running
            ctx.gateway_pid = g.pid
            ctx.gateway_uptime_s = g.uptime_seconds
            if not g.is_running:
                log.warning("gateway not running — restarting")
                if watchdog.restart_gateway():
                    ctx.alerts.append(f"restarted gateway @ {time.strftime('%H:%M:%S')}")
                    ctx.alerts = ctx.alerts[-100:]

            context_bridge.save_context(ctx)
        except Exception:
            log.exception("watchdog loop error")
        time.sleep(WATCHDOG_INTERVAL)


def _gpu_loop(log: logging.Logger, ctx: context_bridge.SystemContext) -> None:
    while True:
        try:
            gpus = gpu_monitor.query_gpu_status()
            ctx.gpu_temps = {g.index: g.temp_c for g in gpus}
            ctx.gpu_vram_used_pct = {g.index: round(g.vram_used_pct, 1) for g in gpus}
            for g in gpus:
                if g.temp_c >= GPU_TEMP_CRITICAL:
                    log.error("GPU %d %s critical temp: %d C", g.index, g.name, g.temp_c)
                    alert_msg = (
                        f"GPU {g.index} ({g.name}) CRITICAL: {g.temp_c}°C"
                        f" @ {time.strftime('%H:%M:%S')}"
                    )
                    ctx.alerts.append(alert_msg)
                    ctx.alerts = ctx.alerts[-100:]
                    _ntfy_push(
                        title=f"GPU {g.index} Critical Temp",
                        message=alert_msg,
                        priority="urgent",
                    )
                    if PROACTIVE_HIVE_ENABLED:
                        from services.scout_daemon.proactive_hive import maybe_trigger
                        maybe_trigger(
                            reason=f"GPU {g.index} ({g.name}) critical temperature: {g.temp_c}°C",
                            context=alert_msg,
                            gateway_url=GATEWAY_URL,
                            auth_token=GATEWAY_AUTH_TOKEN,
                        )
                elif g.temp_c >= GPU_TEMP_WARN:
                    log.warning("GPU %d %s warm: %d C", g.index, g.name, g.temp_c)

            game = gpu_monitor.detect_game_on_gpu(GAMING_GPU)
            ctx.game_running = game
            ctx.game_gpu = GAMING_GPU if game else None
            context_bridge.save_context(ctx)
        except Exception:
            log.exception("gpu loop error")
        time.sleep(GPU_CHECK_INTERVAL)


def _disk_loop(log: logging.Logger, ctx: context_bridge.SystemContext) -> None:
    while True:
        try:
            disks = system_monitor.check_all_disks()
            ctx.disk_free_gb = {d.drive: d.free_gb for d in disks}
            for d in disks:
                if d.free_gb < DISK_WARN_GB:
                    log.warning(
                        "disk %s low: %.1f GB free (warn threshold %d GB)",
                        d.drive, d.free_gb, DISK_WARN_GB,
                    )
                    alert_msg = (
                        f"disk {d.drive} low: {d.free_gb:.1f}GB free"
                        f" @ {time.strftime('%H:%M:%S')}"
                    )
                    ctx.alerts.append(alert_msg)
                    ctx.alerts = ctx.alerts[-100:]
                    _ntfy_push(
                        title=f"Disk {d.drive} Low Space",
                        message=alert_msg,
                        priority="high",
                    )
                    if PROACTIVE_HIVE_ENABLED:
                        from services.scout_daemon.proactive_hive import maybe_trigger
                        maybe_trigger(
                            reason=f"Disk {d.drive} low space: {d.free_gb:.1f}GB free",
                            context=alert_msg,
                            gateway_url=GATEWAY_URL,
                            auth_token=GATEWAY_AUTH_TOKEN,
                        )
            context_bridge.save_context(ctx)
        except Exception:
            log.exception("disk loop error")
        time.sleep(HEALTH_CHECK_INTERVAL)


def _supervisor_loop(log: logging.Logger, ctx: context_bridge.SystemContext) -> None:
    """HTTP health supervisor for the gateway.

    Polls GET /health every PROBE_INTERVAL_S seconds. After
    FAIL_THRESHOLD consecutive failures, restarts the gateway via
    start-gateway.ps1 — subject to the rolling-window circuit breaker
    (MAX_RESTARTS_PER_HOUR in 60 minutes). Once the circuit trips, stops
    retrying and fires an ntfy alert so the operator can investigate.

    Enabled only when GATEWAY_AUTORESTART=true (the default). Disable
    via SCOUT_GATEWAY_AUTORESTART=false in config/.env.

    This loop intentionally does NOT overlap with the existing
    _watchdog_loop: the watchdog uses Win32_Process to check whether the
    python.exe is alive; the supervisor checks whether the HTTP API is
    answering. They are complementary: the watchdog handles process-not-
    running; the supervisor handles process-running-but-wedged (e.g. the
    Ollama-probe SIGTERM path that exits the process cleanly — the watchdog
    restarts it too, but the supervisor adds the circuit breaker and the
    ntfy escalation when repeated crashes indicate a persistent problem).
    """
    if not GATEWAY_AUTORESTART:
        log.info("gateway supervisor: autorestart disabled (SCOUT_GATEWAY_AUTORESTART=false)")
        return

    log.info(
        "gateway supervisor: starting (probe=%.0fs, fail_threshold=%d, "
        "max_restarts=%d/%.0fm)",
        gateway_supervisor.PROBE_INTERVAL_S,
        gateway_supervisor.FAIL_THRESHOLD,
        gateway_supervisor.MAX_RESTARTS_PER_HOUR,
        gateway_supervisor.CIRCUIT_WINDOW_S / 60,
    )

    cb_state = gateway_supervisor.CircuitBreakerState()
    consecutive_failures = 0

    while True:
        try:
            alive = gateway_supervisor.probe_gateway()
            if alive:
                consecutive_failures = 0
                ctx.gateway_online = True
            else:
                consecutive_failures += 1
                ctx.gateway_online = False
                log.warning(
                    "gateway supervisor: health probe failed (%d/%d)",
                    consecutive_failures, gateway_supervisor.FAIL_THRESHOLD,
                )

            if consecutive_failures >= gateway_supervisor.FAIL_THRESHOLD:
                if cb_state.tripped:
                    # Circuit already open — don't spam the log every probe.
                    pass
                else:
                    allowed, reason = gateway_supervisor.check_and_record(cb_state)
                    if allowed:
                        log.warning(
                            "gateway supervisor: %d consecutive failures — "
                            "restarting gateway. %s",
                            consecutive_failures, reason,
                        )
                        ctx.alerts.append(
                            f"gateway supervisor: restarting after "
                            f"{consecutive_failures} health failures "
                            f"@ {time.strftime('%H:%M:%S')} — {reason}"
                        )
                        ctx.alerts = ctx.alerts[-100:]
                        _ntfy_push(
                            title="Gateway supervisor: restarting gateway",
                            message=(
                                f"{consecutive_failures} consecutive health failures. "
                                f"{reason}"
                            ),
                            priority="high",
                        )
                        restarted = watchdog.restart_gateway()
                        log.info(
                            "gateway supervisor: restart_gateway() returned %s",
                            restarted,
                        )
                        # Reset failure counter — give the new process time
                        # to boot before counting failures again.
                        consecutive_failures = 0
                    else:
                        # Circuit just tripped or was already open.
                        log.error(
                            "gateway supervisor: circuit breaker open — %s. "
                            "Manual intervention required.",
                            reason,
                        )
                        ctx.alerts.append(
                            f"gateway supervisor CIRCUIT OPEN: {reason} "
                            f"@ {time.strftime('%H:%M:%S')}"
                        )
                        ctx.alerts = ctx.alerts[-100:]
                        _ntfy_push(
                            title="Gateway supervisor: CIRCUIT OPEN",
                            message=(
                                f"Gateway auto-restart circuit is open. "
                                f"{reason}. "
                                "Manual intervention required."
                            ),
                            priority="urgent",
                        )

            context_bridge.save_context(ctx)
        except Exception:
            log.exception("gateway supervisor loop error")
        time.sleep(gateway_supervisor.PROBE_INTERVAL_S)


def main() -> None:
    log = _setup_logging()
    log.info("scout-daemon starting")
    ctx = context_bridge.load_context()
    ctx.daemon_online = True
    ctx.alerts = []
    context_bridge.save_context(ctx)

    sysmon_rpc.start_in_background()

    threads = [
        threading.Thread(target=_watchdog_loop, args=(log, ctx), daemon=True, name="watchdog"),
        threading.Thread(target=_gpu_loop, args=(log, ctx), daemon=True, name="gpu-monitor"),
        threading.Thread(target=_disk_loop, args=(log, ctx), daemon=True, name="disk-monitor"),
        threading.Thread(target=_supervisor_loop, args=(log, ctx), daemon=True, name="gw-supervisor"),
    ]
    for t in threads:
        t.start()

    log.info("scout-daemon running — RPC on 127.0.0.1:8767, %d worker threads", len(threads))
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("scout-daemon shutting down")
        ctx.daemon_online = False
        context_bridge.save_context(ctx)


if __name__ == "__main__":
    main()
