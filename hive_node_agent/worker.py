"""Long-poll worker — fetches jobs and runs them through runtime adapters.

The loop:
    1. GET /v1/jobs/next?caps=...&vram_mb=... (long-poll, ~30s).
    2. If 204 -> repoll immediately. If 200 -> dispatch to adapter.
    3. Adapter returns RuntimeResult; worker POSTs /v1/jobs/{id}/result.
    4. On any unhandled exception, log + back off (5s, 30s, 5m, 30m).

The worker is *passive* about restarts — when the gateway comes back up
the next long-poll just succeeds. No reconnection logic needed beyond
backoff on HTTP failure.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable
from urllib.parse import urlencode

from hive_node_agent.client import get_json, post_json
from hive_node_agent.config import NodeAgentConfig
from hive_node_agent.runtimes import RUNTIMES, RuntimeResult, adapter_for_kind


log = logging.getLogger("hive_node_agent.worker")


def _next_url(cfg: NodeAgentConfig, caps: set[str], vram_free_mb: int) -> str:
    qs = urlencode({
        "caps": ",".join(sorted(caps)),
        "vram_mb": int(vram_free_mb),
    })
    return cfg.host_url.rstrip("/") + "/v1/jobs/next?" + qs


def _result_url(cfg: NodeAgentConfig, job_id: str) -> str:
    return cfg.host_url.rstrip("/") + f"/v1/jobs/{job_id}/result"


async def poll_once(
    cfg: NodeAgentConfig,
    *,
    caps: set[str],
    vram_free_mb: int,
) -> dict[str, Any] | None:
    """One long-poll. Returns the job dict on hit, None on 204."""
    if not cfg.paired:
        raise RuntimeError("agent not paired — cannot poll")
    return await get_json(
        _next_url(cfg, caps, vram_free_mb),
        token=cfg.token,
        # 35s = 30s server long-poll + 5s slack.
        timeout_s=35.0,
    )


async def _execute_job(job: dict[str, Any]) -> RuntimeResult:
    kind = str(job.get("kind") or "")
    payload = job.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        adapter = adapter_for_kind(kind)
    except KeyError as e:
        return RuntimeResult(
            status="error", output={}, duration_ms=0,
            error=f"no adapter for kind '{kind}': {e}",
        )
    try:
        return await adapter.run(payload)
    except Exception as e:  # noqa: BLE001 — adapter must not crash the loop
        log.exception("adapter %s crashed", adapter.name)
        return RuntimeResult(
            status="error", output={}, duration_ms=0,
            error=f"adapter crashed: {e}",
        )


async def _post_result(
    cfg: NodeAgentConfig, job_id: str, result: RuntimeResult,
) -> None:
    payload = {
        "status": result.status,
        "output": result.output,
        "error": result.error,
        "duration_ms": int(result.duration_ms),
    }
    try:
        await post_json(
            _result_url(cfg, job_id), payload,
            token=cfg.token, timeout_s=10.0,
        )
    except Exception as e:  # noqa: BLE001
        # Result delivery failed. The host's heartbeat-miss sweep will
        # eventually requeue this job once we go offline. Best we can
        # do here is log.
        log.warning("result delivery failed for %s: %s", job_id, e)


CapabilitiesProvider = Callable[[], dict[str, Any]]


async def run_worker_loop(
    cfg: NodeAgentConfig,
    *,
    capabilities_provider: CapabilitiesProvider,
) -> None:
    """Loop until cancelled. `capabilities_provider()` returns
    `{"caps": set[str], "vram_free_mb": int}` — recomputed each poll so
    the worker reflects current GPU headroom."""
    if not cfg.paired:
        raise RuntimeError("agent not paired — refusing to start worker")
    backoff_s = 0.0
    while True:
        try:
            snap = capabilities_provider()
            caps = set(snap.get("caps") or set())
            vram = int(snap.get("vram_free_mb") or 0)
            job = await poll_once(cfg, caps=caps, vram_free_mb=vram)
            if job is None:
                backoff_s = 0.0
                continue
            result = await _execute_job(job)
            await _post_result(cfg, str(job["id"]), result)
            backoff_s = 0.0
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("worker poll failed: %s", e)
            backoff_s = min(1800.0, max(5.0, backoff_s * 6 if backoff_s else 5.0))
            try:
                await asyncio.sleep(backoff_s)
            except asyncio.CancelledError:
                raise
