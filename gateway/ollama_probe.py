"""Ollama GPU-residency probe (#438).

Defense layer 2 against the env-var-drift regression (#437): the
Ollama tray autostart re-spawns `ollama.exe serve` without
``CUDA_VISIBLE_DEVICES=1,2`` and silently falls back to CPU when GPU0
(the gaming 4080) doesn't have headroom. Symptoms look identical to
VRAM contention but the fix is a relaunch via
``scripts/start-ollama-tuned.cmd``.

Layer 1 is filesystem (the Startup\\Ollama.lnk shortcut now points at
the tuned launcher). Layer 2 is this probe — runs after gateway
prewarm and screams in the log if planner-qwen isn't on GPU. Cheap to
run (one HTTP call to ``/api/ps``) and catches BOTH the env-var-drift
AND any future runtime CPU-residency regression (e.g. #439's
hypothesised VRAM-overflow path).

Probe verdict is stored on ``app_state.ollama_probe_result`` so a
future ``/v1/health`` endpoint or admin UI can surface it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Literal

import httpx


log = logging.getLogger("gateway.ollama_probe")

DEFAULT_OLLAMA_BASE = "http://127.0.0.1:11434"
DEFAULT_RETRIES = 3
DEFAULT_RETRY_DELAY = 2.0


Processor = Literal["gpu", "cpu", "mixed", "missing", "unreachable"]


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    processor: Processor
    gpu_pct: float
    message: str
    model_name: str = ""


async def check_model_on_gpu(
    model_prefix: str,
    *,
    client: httpx.AsyncClient | None = None,
    base_url: str = DEFAULT_OLLAMA_BASE,
    retries: int = DEFAULT_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
) -> ProbeResult:
    """Check whether ``model_prefix`` is fully GPU-resident in Ollama.

    Hits ``GET /api/ps`` and inspects the model entry whose ``name``
    starts with ``model_prefix`` (case-insensitive). Computes
    ``gpu_pct = size_vram / size * 100``:

      * 100% → ``ok=True, processor='gpu'``
      * 0% → ``ok=False, processor='cpu'``
      * partial → ``ok=False, processor='mixed'``
      * missing → retries up to ``retries`` times, then
        ``processor='missing'``
      * HTTP/network error → retries, then ``processor='unreachable'``

    The probe is best-effort: any exception is captured into a failing
    ``ProbeResult`` rather than propagated, so lifespan startup can
    log + continue.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=5.0)

    try:
        last_error: str = ""
        for attempt in range(max(1, retries)):
            try:
                resp = await client.get(f"{base_url}/api/ps")
                if resp.status_code >= 500:
                    last_error = f"ollama returned HTTP {resp.status_code}"
                    await _maybe_sleep(attempt, retries, retry_delay)
                    continue
                resp.raise_for_status()
                try:
                    payload = resp.json()
                except (json.JSONDecodeError, ValueError) as e:
                    last_error = f"ollama /api/ps returned non-JSON: {e}"
                    await _maybe_sleep(attempt, retries, retry_delay)
                    continue
            except httpx.HTTPError as e:
                last_error = f"ollama unreachable: {type(e).__name__}: {e}"
                await _maybe_sleep(attempt, retries, retry_delay)
                continue

            entry = _find_model(payload, model_prefix)
            if entry is None:
                last_error = (
                    f"model {model_prefix!r} not loaded in /api/ps "
                    f"(attempt {attempt + 1}/{retries})"
                )
                await _maybe_sleep(attempt, retries, retry_delay)
                continue

            return _verdict_from_entry(entry, model_prefix)

        # Retries exhausted. Distinguish "model missing from /api/ps"
        # from "couldn't even talk to Ollama" — operator action differs.
        is_transport_error = (
            "unreachable" in last_error
            or "non-JSON" in last_error
            or "HTTP " in last_error
        )
        if is_transport_error:
            return ProbeResult(
                ok=False,
                processor="unreachable",
                gpu_pct=0.0,
                message=last_error,
            )
        return ProbeResult(
            ok=False,
            processor="missing",
            gpu_pct=0.0,
            message=last_error or f"model {model_prefix!r} not found",
        )
    finally:
        if own_client and client is not None:
            await client.aclose()


def _find_model(payload: object, prefix: str) -> dict | None:
    if not isinstance(payload, dict):
        return None
    models = payload.get("models")
    if not isinstance(models, list):
        return None
    pfx = prefix.lower()
    for m in models:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or m.get("model") or "").lower()
        if name.startswith(pfx):
            return m
    return None


def _verdict_from_entry(entry: dict, model_prefix: str) -> ProbeResult:
    name = str(entry.get("name") or entry.get("model") or model_prefix)
    size = _safe_int(entry.get("size"))
    size_vram = _safe_int(entry.get("size_vram"))
    if size <= 0:
        return ProbeResult(
            ok=False,
            processor="unreachable",
            gpu_pct=0.0,
            message=f"model {name!r} has invalid size={size}",
            model_name=name,
        )
    gpu_pct = (size_vram / size) * 100.0
    if size_vram >= size:
        return ProbeResult(
            ok=True,
            processor="gpu",
            gpu_pct=100.0,
            message=f"{name} is 100% GPU-resident",
            model_name=name,
        )
    if size_vram <= 0:
        return ProbeResult(
            ok=False,
            processor="cpu",
            gpu_pct=0.0,
            message=(
                f"{name} is 100% CPU — Ollama probably started without "
                f"CUDA_VISIBLE_DEVICES=1,2 (see #437)"
            ),
            model_name=name,
        )
    return ProbeResult(
        ok=False,
        processor="mixed",
        gpu_pct=gpu_pct,
        message=(
            f"{name} is partially offloaded ({gpu_pct:.0f}% GPU / "
            f"{100 - gpu_pct:.0f}% CPU) — VRAM headroom too tight"
        ),
        model_name=name,
    )


def _safe_int(v: object) -> int:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


async def _maybe_sleep(attempt: int, retries: int, delay: float) -> None:
    if delay <= 0:
        return
    if attempt + 1 >= retries:
        return
    await asyncio.sleep(delay)
