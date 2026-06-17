"""End-to-end: agent probe → pair via /v1/pair/node → heartbeat → admin sees node.

Runs entirely in-process with TestClient + a monkey-patched httpx so we
don't need a live network listener.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from hive_node_agent.config import NodeAgentConfig
from hive_node_agent.heartbeat import send_one
from hive_node_agent.pairing import pair_with_host


@pytest.mark.asyncio
async def test_agent_pair_and_heartbeat_e2e(
    client: TestClient,
    paired_token: tuple[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, owner_token = paired_token

    # Owner mints an invite.
    r = client.post(
        "/v1/invites",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    code = r.json()["code"]

    # Wire post_json to use the FastAPI TestClient instead of real httpx.
    async def fake_post(
        url: str, payload: dict[str, Any], *, token: str | None = None,
        timeout_s: float = 10.0,
    ) -> dict[str, Any]:
        # Strip the host portion — TestClient uses path.
        from urllib.parse import urlparse
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

    monkeypatch.setattr("hive_node_agent.pairing.post_json", fake_post)
    monkeypatch.setattr("hive_node_agent.heartbeat.post_json", fake_post)

    # Agent pairs.
    cfg = NodeAgentConfig(state_dir=tmp_path)
    cfg = await pair_with_host(
        cfg,
        host_url="http://testserver",
        code=code,
        name="rtx-rig",
        capabilities={
            "agent_version": "0.1.0",
            "ram_total_gb": 64,
            "labels": ["livingroom"],
        },
    )
    assert cfg.paired

    # Owner can list and see the node.
    r = client.get(
        "/v1/nodes",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert any(n["id"] == cfg.node_id for n in r.json())

    # Agent heartbeats with updated values.
    result = await send_one(cfg, capabilities={
        "agent_version": "0.1.0",
        "ram_total_gb": 64,
        "ram_free_gb": 30.0,
        "labels": ["livingroom"],
    })
    assert result["ok"] is True

    # Owner sees updated capabilities.
    r = client.get(
        f"/v1/nodes/{cfg.node_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    body = r.json()
    assert body["status"] == "online"
    assert body["capabilities"]["ram_free_gb"] == 30.0
