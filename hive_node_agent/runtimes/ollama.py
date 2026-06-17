"""Ollama runtime adapter.

Talks to the local Ollama HTTP server (default `http://127.0.0.1:11434`).
Phase 2 supports `ollama.generate` (single-shot generate); Phase 3 will
add streaming + `embed`. The adapter does NOT manage Ollama's process
lifecycle — Ollama runs as its own service installed on the node.
`start()` and `stop()` are therefore no-ops.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from hive_node_agent.runtimes import RuntimeAdapter, RuntimeResult


DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT_S = 600.0  # diffusion-class jobs may run minutes


class OllamaAdapter(RuntimeAdapter):
    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    async def probe(self) -> dict[str, Any]:
        url = f"{self._base}/api/tags"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
        except (httpx.HTTPError, OSError):
            return {"installed": False}
        if resp.status_code != 200:
            return {"installed": False}
        try:
            data = resp.json()
        except ValueError:
            return {"installed": False}
        models = [m.get("name", "") for m in data.get("models", []) if m]
        return {
            "installed": True,
            "version": "",  # /api/version exists but is optional
            "models": [m for m in models if m],
        }

    async def start(self) -> None:
        # Ollama runs as a system service on the node — adapter does not
        # control it. Kept as a no-op so the contract is uniform.
        return None

    async def stop(self) -> None:
        return None

    async def run(self, payload: dict[str, Any]) -> RuntimeResult:
        model = str(payload.get("model") or "").strip()
        prompt = str(payload.get("prompt") or "")
        if not model:
            return RuntimeResult(
                status="error", output={}, duration_ms=0,
                error="missing 'model' in payload",
            )
        if not prompt:
            return RuntimeResult(
                status="error", output={}, duration_ms=0,
                error="missing 'prompt' in payload",
            )

        body: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
        # Pass-through optional knobs.
        for k in ("system", "template", "options", "format", "raw"):
            if k in payload:
                body[k] = payload[k]

        url = f"{self._base}/api/generate"
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=body)
        except (httpx.HTTPError, OSError) as e:
            elapsed = int((time.monotonic() - t0) * 1000)
            return RuntimeResult(
                status="error", output={}, duration_ms=elapsed,
                error=f"ollama unreachable: {e}",
            )
        elapsed = int((time.monotonic() - t0) * 1000)
        if resp.status_code != 200:
            try:
                err_body = resp.json()
                detail = err_body.get("error") or resp.text
            except ValueError:
                detail = resp.text
            return RuntimeResult(
                status="error", output={}, duration_ms=elapsed,
                error=f"ollama HTTP {resp.status_code}: {detail}",
            )
        try:
            data = resp.json()
        except ValueError:
            return RuntimeResult(
                status="error", output={}, duration_ms=elapsed,
                error="ollama returned non-JSON",
            )
        return RuntimeResult(
            status="done",
            output={
                "model": data.get("model", model),
                "response": data.get("response", ""),
                "done": bool(data.get("done", True)),
                "eval_count": data.get("eval_count"),
                "eval_duration": data.get("eval_duration"),
            },
            duration_ms=elapsed,
        )
