"""Smoke test: AppState gets NodeRegistry + InviteBroker on startup."""

from __future__ import annotations

from fastapi.testclient import TestClient

from gateway.worker_pool.invites import InviteBroker
from gateway.worker_pool.registry import NodeRegistry


def test_app_state_has_node_registry(client: TestClient) -> None:
    st = client.app.state.ai_team
    assert isinstance(st.node_registry, NodeRegistry)
    assert isinstance(st.node_invites, InviteBroker)
