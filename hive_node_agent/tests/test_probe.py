"""Tests for probe.collect — schema + GPU/CPU/RAM/disk fields."""

from __future__ import annotations

from unittest.mock import patch

from hive_node_agent.probe import collect, parse_nvidia_smi_csv


def test_collect_returns_required_keys() -> None:
    snapshot = collect()
    for key in (
        "agent_version", "os", "cpu", "ram_total_gb", "ram_free_gb",
        "gpus", "disk_free_gb", "runtimes", "labels",
    ):
        assert key in snapshot, f"missing key: {key}"
    assert isinstance(snapshot["gpus"], list)
    assert isinstance(snapshot["runtimes"], dict)


def test_parse_nvidia_smi_csv_single_gpu() -> None:
    raw = "0, NVIDIA GeForce RTX 4090, 24576, 22100, 555.99, 12.4\n"
    parsed = parse_nvidia_smi_csv(raw)
    assert len(parsed) == 1
    g = parsed[0]
    assert g["index"] == 0
    assert g["name"] == "NVIDIA GeForce RTX 4090"
    assert g["vram_total_mb"] == 24576
    assert g["vram_free_mb"] == 22100
    assert g["driver"] == "555.99"
    assert g["cuda"] == "12.4"


def test_parse_nvidia_smi_csv_multi_gpu() -> None:
    raw = (
        "0, RTX 4090, 24576, 22100, 555.99, 12.4\n"
        "1, RTX 3090, 24576, 19000, 555.99, 12.4\n"
    )
    parsed = parse_nvidia_smi_csv(raw)
    assert [g["index"] for g in parsed] == [0, 1]


def test_parse_nvidia_smi_csv_handles_empty() -> None:
    assert parse_nvidia_smi_csv("") == []


def test_collect_with_no_nvidia_smi_returns_empty_gpus() -> None:
    with patch("hive_node_agent.probe._run_nvidia_smi", return_value=None):
        snap = collect()
    assert snap["gpus"] == []


def test_collect_runtime_fields_have_installed_flag() -> None:
    snap = collect()
    for name in ("ollama", "comfy", "embed", "i2v"):
        assert name in snap["runtimes"]
        assert "installed" in snap["runtimes"][name]
