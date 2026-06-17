"""Smoke test for the require_node FastAPI dependency."""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from gateway.deps import require_node


def _build_probe_app(parent_app) -> FastAPI:
    """Mount a tiny ad-hoc router on the existing test app."""
    router = APIRouter()

    @router.get("/_probe/node")
    def probe(node=Depends(require_node)):
        return {"node_id": node.id}

    parent_app.include_router(router)
    return parent_app


def _pair_node(client: TestClient, owner_token: str) -> tuple[str, str]:
    r = client.post(
        "/v1/invites",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    code = r.json()["code"]
    r = client.post("/v1/pair/node", json={
        "code": code, "name": "probe-node",
        "capabilities": {"agent_version": "0.1.0"},
    })
    body = r.json()
    return body["node_id"], body["token"]


def test_require_node_rejects_owner_token(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _build_probe_app(client.app)
    _, owner_token = paired_token
    r = client.get(
        "/_probe/node",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert r.status_code == 401


def test_require_node_accepts_node_token(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _build_probe_app(client.app)
    _, owner_token = paired_token
    node_id, node_token = _pair_node(client, owner_token)
    r = client.get(
        "/_probe/node",
        headers={"Authorization": f"Bearer {node_token}"},
    )
    assert r.status_code == 200
    assert r.json()["node_id"] == node_id
