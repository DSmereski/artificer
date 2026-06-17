"""Mid-run Ollama GPU-residency watchdog (#473).

Boot-time defense (#438/#472) catches the most common drift mode (Ollama
tray autostart respawning without ``CUDA_VISIBLE_DEVICES`` set correctly), but
runtime regressions can still flip planner-qwen onto CPU after the gateway
has been up for hours:

  * Ollama service restart (Windows Update, manual kill, OOM)
  * VRAM-overflow eviction (#439's hypothesised path)
  * Operator-induced misconfig (model pull, second model loaded)

This watchdog re-runs the same ``check_model_on_gpu`` probe on a slow
loop (default every 5 minutes) and applies the same abort policy as
boot: definite ``cpu`` / ``mixed`` verdicts SIGTERM the gateway so
helper timeouts stop accumulating; transient verdicts only warn.

Cheap to run — one HTTP call per tick. Doesn't add latency to user
turns. Verdict stashed on ``app_state.ollama_probe_result`` (same field
as boot probe) so /v1/health surfaces the freshest read.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import TYPE_CHECKING, Callable

from gateway.ollama_probe import ProbeResult, check_model_on_gpu

if TYPE_CHECKING:
    from gateway.deps import AppState


log = logging.getLogger("gateway.ollama_watchdog")

DEFAULT_INTERVAL_S = 300.0  # 5 minutes
DEFAULT_PROBE_PREFIX = "planner-qwen"


async def watchdog_loop(
    app_state: "AppState",
    *,
    interval_s: float = DEFAULT_INTERVAL_S,
    probe_model_prefix: str = DEFAULT_PROBE_PREFIX,
    abort_on_bad_verdict: bool = True,
    probe_fn: Callable[..., "asyncio.Future[ProbeResult]"] | None = None,
    abort_fn: Callable[[str, str], None] | None = None,
) -> None:
    """Run residency probe forever on a slow tick.

    First tick fires after ``interval_s`` so it doesn't race the boot
    probe (which already fires inside ``_prewarm_then_probe_planner_model``).

    A bad verdict only triggers abort if it's *new* — repeated bad
    verdicts log at WARNING but don't re-SIGTERM (one is enough; the
    operator either fixed it or the process is already shutting down).
    """
    probe = probe_fn or check_model_on_gpu
    abort = abort_fn or _abort_gateway_for_bad_watchdog
    last_processor: str | None = None

    while True:
        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            raise

        try:
            result = await probe(probe_model_prefix)
        except Exception as e:  # noqa: BLE001
            log.warning("ollama watchdog: probe raised %s; will retry", e)
            continue

        app_state.ollama_probe_result = result
        proc = result.processor

        if proc == "gpu":
            if last_processor and last_processor != "gpu":
                log.warning(
                    "ollama watchdog: %s recovered to GPU (was %s)",
                    probe_model_prefix, last_processor,
                )
            else:
                log.debug(
                    "ollama watchdog: %s 100%% GPU-resident",
                    probe_model_prefix,
                )
        elif proc == "cpu" or proc == "mixed":
            severity_change = last_processor != proc
            if severity_change:
                log.critical(
                    "ollama watchdog: %s drifted to %s mid-run "
                    "(gpu_pct=%.0f). Detail: %s",
                    probe_model_prefix, proc, result.gpu_pct, result.message,
                )
                if abort_on_bad_verdict:
                    abort(proc, result.message)
            else:
                log.warning(
                    "ollama watchdog: %s still on %s (no abort — already "
                    "fired). Detail: %s",
                    probe_model_prefix, proc, result.message,
                )
        elif proc == "missing":
            log.warning(
                "ollama watchdog: %s not loaded (model unloaded?). "
                "Detail: %s",
                probe_model_prefix, result.message,
            )
        else:  # unreachable
            log.warning(
                "ollama watchdog: %s — %s", proc, result.message,
            )

        last_processor = proc


def _abort_gateway_for_bad_watchdog(processor: str, detail: str) -> None:
    """Mid-run abort: SIGTERM the gateway so uvicorn drains cleanly.

    Mirrors ``gateway.app._abort_gateway_for_bad_probe`` but with a
    watchdog-specific log message so operators can tell boot vs. runtime
    aborts apart in journalctl. Reversible via
    ``ollama_watchdog_abort_on_bad_verdict: false`` in gateway.yaml.
    """
    log.critical(
        "ollama watchdog: aborting gateway mid-run (processor=%s) — "
        "Ollama drifted off GPU after boot. Fix Ollama and restart. "
        "Set ollama_watchdog_abort_on_bad_verdict=false in gateway.yaml "
        "to disable. Detail: %s",
        processor, detail,
    )
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except OSError as e:
        log.error("failed to send SIGTERM for watchdog abort: %s", e)
