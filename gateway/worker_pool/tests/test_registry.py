"""CRUD + lookup tests for NodeRegistry."""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.worker_pool.registry import NodeRegistry


@pytest.fixture
def reg(tmp_path: Path) -> NodeRegistry:
    return NodeRegistry.open(tmp_path / "hive_nodes.db")


def test_add_then_get(reg: NodeRegistry) -> None:
    node = reg.add(
        name="rtx4090-rig",
        token="raw-token-abc",
        labels=("livingroom",),
    )
    assert node.id.startswith("n_")
    assert node.name == "rtx4090-rig"
    assert node.token_hash != "raw-token-abc"  # hashed
    assert node.labels == ("livingroom",)
    assert reg.get(node.id) == node


def test_list_excludes_revoked(reg: NodeRegistry) -> None:
    a = reg.add(name="a", token="tok-a")
    b = reg.add(name="b", token="tok-b")
    reg.revoke(a.id)
    ids = {n.id for n in reg.list_active()}
    assert ids == {b.id}
    # list() includes everything for audit
    assert {n.id for n in reg.list()} == {a.id, b.id}


def test_verify_token_returns_node(reg: NodeRegistry) -> None:
    node = reg.add(name="a", token="raw-token-abc")
    found = reg.verify_token("raw-token-abc")
    assert found is not None and found.id == node.id


def test_verify_token_rejects_revoked(reg: NodeRegistry) -> None:
    node = reg.add(name="a", token="raw-token-abc")
    reg.revoke(node.id)
    assert reg.verify_token("raw-token-abc") is None


def test_record_heartbeat_updates_last_seen_and_caps(reg: NodeRegistry) -> None:
    node = reg.add(name="a", token="tok-a")
    caps = {"agent_version": "0.1.0", "ram_total_gb": 64}
    reg.record_heartbeat(node.id, caps)
    n = reg.get(node.id)
    assert n is not None and n.agent_version == "0.1.0"
    assert n.last_seen > node.last_seen
    history = reg.recent_heartbeats(node.id, limit=5)
    assert len(history) == 1
    assert history[0]["ram_total_gb"] == 64


def test_record_heartbeat_rejects_unknown_node(reg: NodeRegistry) -> None:
    with pytest.raises(ValueError, match="unknown node id"):
        reg.record_heartbeat("n_does_not_exist", {"agent_version": "0.1.0"})


def test_purge_removes_row(reg: NodeRegistry) -> None:
    node = reg.add(name="a", token="tok-a")
    assert reg.purge(node.id) is True
    assert reg.get(node.id) is None
    assert reg.purge(node.id) is False  # idempotent
