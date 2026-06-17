"""Shared pytest fixtures for vault_writer tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Path:
    """An empty vault directory layout."""
    for d in ("canon", "people", "sessions", "ops", "journals",
              "knowledge", "system", "projects", "tools"):
        (tmp_path / d).mkdir()
    return tmp_path


@pytest.fixture
def tmp_config_yaml(tmp_path: Path, tmp_vault: Path) -> Path:
    """A valid config.yaml pointing at a temp vault."""
    path = tmp_path / "vault-writer.yaml"
    path.write_text(
        f"""vault_path: {tmp_vault}
daemon_bind_host: 127.0.0.1
daemon_bind_port: 0
ollama_url: http://localhost:11434
embedding_model: nomic-embed-text
embedding_dimension: 768
gitea:
  remote: ""
  token_env: GITEA_TOKEN
  push_on_write: false
  batch_window_seconds: 5
search:
  default_k: 5
  min_score: 0.4
scan:
  initial_full_scan: true
  periodic_seconds: 300
""",
        encoding="utf-8",
    )
    return path
