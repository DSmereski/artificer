"""Tests for pairing.pair_with_host — wraps /v1/pair/node + persists config."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from hive_node_agent.config import NodeAgentConfig
from hive_node_agent.pairing import pair_with_host


@pytest.mark.asyncio
async def test_pairing_persists_token_and_node_id(tmp_path: Path) -> None:
    cfg = NodeAgentConfig(state_dir=tmp_path)
    fake_resp = {"node_id": "n_abc", "token": "tok-xyz", "name": "rtx-rig"}
    with patch(
        "hive_node_agent.pairing.post_json",
        new=AsyncMock(return_value=fake_resp),
    ) as mock:
        result = await pair_with_host(
            cfg,
            host_url="http://192.0.2.10:8766",
            code="123-456",
            name="rtx-rig",
            capabilities={"agent_version": "0.1.0"},
        )
    assert result.host_url == "http://192.0.2.10:8766"
    assert result.token == "tok-xyz"
    assert result.node_id == "n_abc"
    # Reload from disk to confirm persistence.
    reloaded = NodeAgentConfig.load(tmp_path)
    assert reloaded.token == "tok-xyz"
    # Endpoint URL is correct.
    mock.assert_awaited_once()
    called_url = mock.await_args.args[0]
    assert called_url.endswith("/v1/pair/node")


@pytest.mark.asyncio
async def test_pairing_strips_trailing_slash_from_host(tmp_path: Path) -> None:
    cfg = NodeAgentConfig(state_dir=tmp_path)
    fake_resp = {"node_id": "n_abc", "token": "tok", "name": "n"}
    with patch(
        "hive_node_agent.pairing.post_json",
        new=AsyncMock(return_value=fake_resp),
    ) as mock:
        await pair_with_host(
            cfg,
            host_url="http://x:8766/",
            code="000-000",
            name="n",
            capabilities={},
        )
    called_url = mock.await_args.args[0]
    assert called_url == "http://x:8766/v1/pair/node"


@pytest.mark.asyncio
async def test_pairing_persists_labels_from_capabilities(tmp_path: Path) -> None:
    cfg = NodeAgentConfig(state_dir=tmp_path)
    fake_resp = {"node_id": "n_abc", "token": "tok", "name": "rtx-rig"}
    with patch(
        "hive_node_agent.pairing.post_json",
        new=AsyncMock(return_value=fake_resp),
    ):
        result = await pair_with_host(
            cfg,
            host_url="http://x:8766",
            code="000-000",
            name="rtx-rig",
            capabilities={"agent_version": "0.1.0", "labels": ["smoke", "rtx4090"]},
        )
    assert result.labels == ("smoke", "rtx4090")
    reloaded = NodeAgentConfig.load(tmp_path)
    assert reloaded.labels == ("smoke", "rtx4090")
