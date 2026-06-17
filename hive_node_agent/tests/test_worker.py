"""Tests for worker.run_worker_loop — single iteration via mocked client."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from hive_node_agent.config import NodeAgentConfig
from hive_node_agent.runtimes import (
    RUNTIMES,
    RuntimeAdapter,
    RuntimeResult,
    register_adapter,
)
from hive_node_agent.worker import (
    poll_once,
    run_worker_loop,
)


@pytest.fixture(autouse=True)
def _clear_runtimes():
    RUNTIMES.clear()
    yield
    RUNTIMES.clear()


class _ScriptedAdapter(RuntimeAdapter):
    name = "ollama"

    def __init__(self, result: RuntimeResult) -> None:
        self._result = result
        self.calls: list[dict] = []

    async def probe(self) -> dict:
        return {"installed": True}

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def run(self, payload: dict) -> RuntimeResult:
        self.calls.append(payload)
        return self._result


@pytest.mark.asyncio
async def test_poll_once_returns_none_on_204(tmp_path: Path) -> None:
    cfg = NodeAgentConfig(
        state_dir=tmp_path, host_url="http://x:8766",
        token="tok", node_id="n_a",
    )
    transport_calls: list[tuple[str, str, dict | None]] = []

    async def fake_get_json(url, *, token=None, timeout_s=35.0):
        transport_calls.append(("GET", url, None))
        # 204 sentinel: client returns None.
        return None

    with patch(
        "hive_node_agent.worker.get_json", new=fake_get_json,
    ):
        job = await poll_once(cfg, caps={"ollama"}, vram_free_mb=20000)
    assert job is None
    assert transport_calls
    url = transport_calls[0][1]
    assert "/v1/jobs/next" in url
    assert "caps=ollama" in url
    assert "vram_mb=20000" in url


@pytest.mark.asyncio
async def test_poll_once_returns_job_dict_on_200(tmp_path: Path) -> None:
    cfg = NodeAgentConfig(
        state_dir=tmp_path, host_url="http://x:8766",
        token="tok", node_id="n_a",
    )
    fake_job = {
        "id": "j_abc",
        "kind": "ollama.generate",
        "payload": {"model": "qwen2.5:1.5b", "prompt": "hi"},
    }

    async def fake_get_json(url, *, token=None, timeout_s=35.0):
        return fake_job

    with patch("hive_node_agent.worker.get_json", new=fake_get_json):
        job = await poll_once(cfg, caps={"ollama"}, vram_free_mb=20000)
    assert job == fake_job


@pytest.mark.asyncio
async def test_run_worker_loop_runs_then_posts_result(
    tmp_path: Path,
) -> None:
    """Single-iteration worker: poll returns a job, adapter runs it,
    worker posts the result, then we cancel the loop."""
    cfg = NodeAgentConfig(
        state_dir=tmp_path, host_url="http://x:8766",
        token="tok", node_id="n_a",
    )
    fake_job = {
        "id": "j_abc",
        "kind": "ollama.generate",
        "payload": {"model": "qwen2.5:1.5b", "prompt": "hi"},
    }
    sent_results: list[tuple[str, dict]] = []
    poll_count = {"n": 0}

    # Register a scripted adapter under name='ollama'.
    register_adapter(_ScriptedAdapter(RuntimeResult(
        status="done", output={"response": "ok"}, duration_ms=11,
    )))

    async def fake_get_json(url, *, token=None, timeout_s=35.0):
        # sleep(0) yields to the event loop so the test can observe
        # sent_results between poll iterations. Without it the worker's
        # tight `if job is None: continue` loop never gives control back.
        await asyncio.sleep(0)
        poll_count["n"] += 1
        # First call returns the job; subsequent calls return None
        # (mimic empty queue) so the loop keeps spinning.
        return fake_job if poll_count["n"] == 1 else None

    async def fake_post_json(url, payload, *, token=None, timeout_s=10.0):
        sent_results.append((url, payload))
        return {"ok": True}

    with patch("hive_node_agent.worker.get_json", new=fake_get_json), \
         patch("hive_node_agent.worker.post_json", new=fake_post_json):
        task = asyncio.create_task(
            run_worker_loop(cfg, capabilities_provider=lambda: {
                "caps": {"ollama"}, "vram_free_mb": 20000,
            }),
        )
        # Give the loop time to poll once + post once.
        for _ in range(40):
            await asyncio.sleep(0.02)
            if sent_results:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert sent_results, "expected exactly one result POST"
    url, payload = sent_results[0]
    assert url.endswith("/v1/jobs/j_abc/result")
    assert payload["status"] == "done"
    assert payload["output"] == {"response": "ok"}
    assert payload["duration_ms"] == 11


@pytest.mark.asyncio
async def test_unknown_kind_reports_error(tmp_path: Path) -> None:
    """If no adapter is registered for the job kind, worker returns an
    error result instead of crashing the loop."""
    cfg = NodeAgentConfig(
        state_dir=tmp_path, host_url="http://x:8766",
        token="tok", node_id="n_a",
    )
    fake_job = {
        "id": "j_abc",
        "kind": "no-such.verb",
        "payload": {},
    }
    # Make sure 'no-such' is not in the registry.
    RUNTIMES.pop("no-such", None)
    sent_results: list[tuple[str, dict]] = []
    poll_count = {"n": 0}

    async def fake_get_json(url, *, token=None, timeout_s=35.0):
        await asyncio.sleep(0)  # yield so test polling can run
        poll_count["n"] += 1
        return fake_job if poll_count["n"] == 1 else None

    async def fake_post_json(url, payload, *, token=None, timeout_s=10.0):
        sent_results.append((url, payload))
        return {"ok": True}

    with patch("hive_node_agent.worker.get_json", new=fake_get_json), \
         patch("hive_node_agent.worker.post_json", new=fake_post_json):
        task = asyncio.create_task(
            run_worker_loop(cfg, capabilities_provider=lambda: {
                "caps": {"ollama"}, "vram_free_mb": 0,
            }),
        )
        for _ in range(40):
            await asyncio.sleep(0.02)
            if sent_results:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert sent_results
    _, payload = sent_results[0]
    assert payload["status"] == "error"
    assert "no-such" in payload["error"].lower()
