"""Tests for heartbeat.send_one + run_heartbeat_loop (single iteration)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from hive_node_agent.config import NodeAgentConfig
from hive_node_agent.heartbeat import run_heartbeat_loop, send_one


@pytest.mark.asyncio
async def test_send_one_posts_to_correct_url(tmp_path: Path) -> None:
    cfg = NodeAgentConfig(
        state_dir=tmp_path,
        host_url="http://127.0.0.1:8766",
        token="tok",
        node_id="n_abc",
    )
    with patch(
        "hive_node_agent.heartbeat.post_json",
        new=AsyncMock(return_value={"ok": True, "server_time": 1.0}),
    ) as mock:
        result = await send_one(cfg, capabilities={"agent_version": "0.1.0"})
    assert result["ok"] is True
    called_url = mock.await_args.args[0]
    assert called_url == "http://127.0.0.1:8766/v1/nodes/n_abc/heartbeat"
    # Auth header populated.
    kwargs = mock.await_args.kwargs
    assert kwargs.get("token") == "tok"


@pytest.mark.asyncio
async def test_send_one_raises_when_unpaired(tmp_path: Path) -> None:
    cfg = NodeAgentConfig(state_dir=tmp_path)
    with pytest.raises(RuntimeError, match="not paired"):
        await send_one(cfg, capabilities={})


@pytest.mark.asyncio
async def test_run_heartbeat_loop_uses_collect_and_can_be_cancelled(
    tmp_path: Path,
) -> None:
    cfg = NodeAgentConfig(
        state_dir=tmp_path,
        host_url="http://x:8766",
        token="tok", node_id="n_abc",
        heartbeat_interval_s=0,
    )
    sent: list[dict] = []

    async def fake_post(url, payload, *, token=None, timeout_s=10.0):
        sent.append(payload)
        return {"ok": True, "server_time": 1.0}

    with patch("hive_node_agent.heartbeat.post_json", new=fake_post):
        task = asyncio.create_task(run_heartbeat_loop(cfg))
        # Let the loop fire at least once.
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert sent, "expected at least one heartbeat to be sent"
    assert sent[0].get("agent_version")
