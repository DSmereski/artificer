"""Tests for gateway.config."""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.config import Config, load_config


def _write(path: Path, **overrides) -> Path:
    defaults = {
        "bind_host": "127.0.0.1",
        "bind_port": 8766,
        "tailscale_bind": None,
        "state_dir": str(path.parent / "state"),
        "vault_writer": {
            "host": "127.0.0.1", "port": 8765,
            "token_path": str(path.parent / "tok"),
        },
        "vault_path": str(path.parent),
    }
    defaults.update(overrides)
    import yaml as _yaml
    path.write_text(_yaml.safe_dump(defaults), encoding="utf-8")
    return path


def test_load_config_defaults_populate(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path / "g.yaml"))
    assert isinstance(cfg, Config)
    assert cfg.bind_host == "127.0.0.1"
    assert cfg.bind_port == 8766
    assert cfg.tailscale_bind is None
    assert cfg.pairing.code_ttl_seconds == 300
    assert cfg.ntfy.enabled is False


def test_load_config_rejects_public_bind(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback or Tailscale"):
        load_config(_write(tmp_path / "g.yaml", bind_host="0.0.0.0"))


def test_load_config_rejects_public_tailscale_bind(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback or Tailscale"):
        load_config(_write(tmp_path / "g.yaml", tailscale_bind="8.8.8.8"))


def test_load_config_accepts_tailscale_cgnat(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path / "g.yaml", tailscale_bind="100.64.1.5"))
    assert cfg.tailscale_bind == "100.64.1.5"


def test_load_config_accepts_bare_hostname(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path / "g.yaml", tailscale_bind="my-desktop"))
    assert cfg.tailscale_bind == "my-desktop"


def test_load_config_bad_port(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bind_port"):
        load_config(_write(tmp_path / "g.yaml", bind_port=99999))
