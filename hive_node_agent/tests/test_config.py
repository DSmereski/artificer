"""Tests for NodeAgentConfig load/persist."""

from __future__ import annotations

from pathlib import Path

from hive_node_agent.config import NodeAgentConfig


def test_default_config(tmp_path: Path) -> None:
    cfg = NodeAgentConfig(state_dir=tmp_path)
    assert cfg.host_url == ""
    assert cfg.token == ""
    assert cfg.node_id == ""
    assert cfg.heartbeat_interval_s == 15


def test_persist_roundtrip(tmp_path: Path) -> None:
    cfg = NodeAgentConfig(
        state_dir=tmp_path,
        host_url="http://127.0.0.1:8766",
        token="abc",
        node_id="n_xyz",
        labels=("livingroom",),
    )
    cfg.save()
    reloaded = NodeAgentConfig.load(tmp_path)
    assert reloaded.host_url == "http://127.0.0.1:8766"
    assert reloaded.token == "abc"
    assert reloaded.node_id == "n_xyz"
    assert reloaded.labels == ("livingroom",)


def test_load_missing_file_returns_defaults(tmp_path: Path) -> None:
    cfg = NodeAgentConfig.load(tmp_path)
    assert cfg.token == ""
    assert cfg.state_dir == tmp_path


def test_paired_property(tmp_path: Path) -> None:
    assert NodeAgentConfig(state_dir=tmp_path).paired is False
    paired = NodeAgentConfig(
        state_dir=tmp_path, host_url="http://x", token="t", node_id="n_1",
    )
    assert paired.paired is True
