"""Tests for vault_writer.config."""

from __future__ import annotations

from pathlib import Path

import pytest

from vault_writer.config import Config, load_config


def test_load_config_returns_typed_object(tmp_config_yaml: Path, tmp_vault: Path) -> None:
    cfg = load_config(tmp_config_yaml)

    assert isinstance(cfg, Config)
    assert cfg.vault_path == tmp_vault
    assert cfg.daemon_bind_host == "127.0.0.1"
    assert cfg.daemon_bind_port == 0
    assert cfg.ollama_url == "http://localhost:11434"
    assert cfg.embedding_model == "nomic-embed-text"
    assert cfg.embedding_dimension == 768
    assert cfg.gitea.push_on_write is False
    assert cfg.search.default_k == 5
    assert cfg.scan.periodic_seconds == 300


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "does-not-exist.yaml")


def test_load_config_vault_path_must_exist(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        f"""vault_path: {tmp_path / 'nope'}
daemon_bind_host: 127.0.0.1
daemon_bind_port: 8765
ollama_url: http://localhost:11434
embedding_model: nomic-embed-text
embedding_dimension: 768
gitea: {{remote: "", token_env: GITEA_TOKEN, push_on_write: false, batch_window_seconds: 5}}
search: {{default_k: 5, min_score: 0.4}}
scan: {{initial_full_scan: true, periodic_seconds: 300}}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="vault_path"):
        load_config(bad)


def _write_min_config(tmp_path: Path, vault: Path, **overrides: str) -> Path:
    fields = {
        "vault_path": str(vault),
        "daemon_bind_host": "127.0.0.1",
        "daemon_bind_port": "8765",
    }
    fields.update(overrides)
    path = tmp_path / "min.yaml"
    lines = [f"{k}: {v}" for k, v in fields.items()]
    path.write_text(
        "\n".join(lines) + "\n"
        "ollama_url: http://localhost:11434\n"
        "embedding_model: nomic-embed-text\n"
        "embedding_dimension: 768\n"
        "gitea: {remote: \"\", token_env: GITEA_TOKEN, push_on_write: false, batch_window_seconds: 5}\n"
        "search: {default_k: 5, min_score: 0.4}\n"
        "scan: {initial_full_scan: true, periodic_seconds: 300}\n",
        encoding="utf-8",
    )
    return path


def test_load_config_rejects_non_loopback_host(tmp_path: Path, tmp_vault: Path) -> None:
    cfg = _write_min_config(tmp_path, tmp_vault, daemon_bind_host="0.0.0.0")
    with pytest.raises(ValueError, match="loopback"):
        load_config(cfg)


def test_load_config_rejects_bad_port(tmp_path: Path, tmp_vault: Path) -> None:
    cfg = _write_min_config(tmp_path, tmp_vault, daemon_bind_port="99999")
    with pytest.raises(ValueError, match="0-65535"):
        load_config(cfg)


def test_load_config_auth_token_path_resolved(tmp_path: Path, tmp_vault: Path) -> None:
    token_file = tmp_path / "tok"
    token_file.write_text("s3cret-value", encoding="utf-8")
    yaml_path = tmp_path / "with-auth.yaml"
    # Single-quoted YAML string so Windows backslashes aren't interpreted
    # as escape sequences.
    yaml_path.write_text(
        f"""vault_path: '{tmp_vault}'
daemon_bind_host: 127.0.0.1
daemon_bind_port: 8765
ollama_url: http://x
embedding_model: m
embedding_dimension: 8
gitea: {{remote: "", token_env: GITEA_TOKEN, push_on_write: false, batch_window_seconds: 5}}
search: {{default_k: 5, min_score: 0.4}}
scan: {{initial_full_scan: true, periodic_seconds: 300}}
auth: {{token_path: '{token_file}'}}
""",
        encoding="utf-8",
    )
    cfg = load_config(yaml_path)
    assert cfg.auth.token_path == token_file
    assert cfg.auth.token() == "s3cret-value"
