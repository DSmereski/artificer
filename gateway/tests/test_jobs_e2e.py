"""End-to-end: owner enqueues an ollama job, agent picks it up, runs it
through a stubbed ollama adapter, posts the result, owner reads it.

Runs entirely in-process. The Ollama HTTP layer is mocked so we don't
need a live model server.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from hive_node_agent.config import NodeAgentConfig
from hive_node_agent.pairing import pair_with_host
from hive_node_agent.runtimes import (
    RUNTIMES,
    RuntimeAdapter,
    RuntimeResult,
    register_adapter,
)
from hive_node_agent.worker import run_worker_loop


@pytest.fixture(autouse=True)
def _clear_runtimes():
    # E2E test registers a stub Ollama adapter; isolate from other
    # tests that may inspect the global registry.
    RUNTIMES.clear()
    yield
    RUNTIMES.clear()


class _StubOllama(RuntimeAdapter):
    name = "ollama"

    async def probe(self) -> dict:
        return {"installed": True, "models": ["qwen2.5:1.5b"]}

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def run(self, payload: dict) -> RuntimeResult:
        return RuntimeResult(
            status="done",
            output={"response": f"echo: {payload.get('prompt', '')}"},
            duration_ms=42,
        )


@pytest.mark.asyncio
async def test_jobs_e2e_owner_to_agent_to_owner(
    client: TestClient,
    paired_token: tuple[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, owner_token = paired_token

    # Make agent's httpx layer go through the FastAPI TestClient.
    async def fake_post_json(
        url: str, payload: dict[str, Any], *,
        token: str | None = None, timeout_s: float = 10.0,
    ) -> dict[str, Any]:
        path = urlparse(url).path
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = client.post(path, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                "fake", request=None, response=resp,  # type: ignore[arg-type]
            )
        return resp.json() if resp.content else {}

    async def fake_get_json(
        url: str, *,
        token: str | None = None, timeout_s: float = 35.0,
    ) -> dict[str, Any] | None:
        # Yield to the event loop so the worker's tight `while True`
        # poll-then-continue path doesn't starve the test's outer
        # `await asyncio.sleep(0.05)` polling. Without this, awaiting
        # a coroutine that doesn't actually I/O is effectively
        # synchronous in CPython asyncio.
        await asyncio.sleep(0)
        # Force timeout=0 so /v1/jobs/next doesn't block.
        from urllib.parse import urlparse, parse_qs, urlencode
        u = urlparse(url)
        qs = parse_qs(u.query)
        qs["timeout"] = ["0"]
        new_query = urlencode({k: v[0] for k, v in qs.items()})
        path = u.path + "?" + new_query if new_query else u.path
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = client.get(path, headers=headers)
        if resp.status_code == 204:
            return None
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                "fake", request=None, response=resp,  # type: ignore[arg-type]
            )
        return resp.json() if resp.content else None

    monkeypatch.setattr("hive_node_agent.pairing.post_json", fake_post_json)
    monkeypatch.setattr("hive_node_agent.heartbeat.post_json", fake_post_json)
    monkeypatch.setattr("hive_node_agent.worker.post_json", fake_post_json)
    monkeypatch.setattr("hive_node_agent.worker.get_json", fake_get_json)

    # Pair the agent.
    r = client.post(
        "/v1/invites",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    code = r.json()["code"]
    cfg = NodeAgentConfig(state_dir=tmp_path)
    cfg = await pair_with_host(
        cfg,
        host_url="http://testserver",
        code=code,
        name="rig-e2e",
        capabilities={"agent_version": "0.1.0", "labels": ["e2e"]},
    )

    register_adapter(_StubOllama())

    # Owner enqueues a job.
    r = client.post(
        "/v1/jobs",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={
            "kind": "ollama.generate",
            "payload": {"model": "qwen2.5:1.5b", "prompt": "say hi"},
            "required_caps": ["ollama"],
        },
    )
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]

    # Spin the worker loop until the job leaves the queue.
    task = asyncio.create_task(
        run_worker_loop(cfg, capabilities_provider=lambda: {
            "caps": {"ollama"}, "vram_free_mb": 20000,
        }),
    )
    try:
        for _ in range(80):
            await asyncio.sleep(0.05)
            r = client.get(
                f"/v1/jobs/{job_id}",
                headers={"Authorization": f"Bearer {owner_token}"},
            )
            assert r.status_code == 200
            if r.json()["status"] == "done":
                break
        else:
            pytest.fail("job never reached 'done'")
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Owner reads result.
    r = client.get(
        f"/v1/jobs/{job_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    body = r.json()
    assert body["status"] == "done"
    assert body["result"] == {"response": "echo: say hi"}
    assert body["duration_ms"] == 42
    assert body["node_id"] == cfg.node_id
