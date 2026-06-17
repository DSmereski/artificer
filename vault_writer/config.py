"""Typed config loader for vault-writer."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class GiteaConfig:
    remote: str
    token_env: str
    push_on_write: bool
    batch_window_seconds: int

    def token(self) -> str | None:
        return os.environ.get(self.token_env)


@dataclass(frozen=True, slots=True)
class SearchConfig:
    default_k: int
    min_score: float


@dataclass(frozen=True, slots=True)
class ScanConfig:
    initial_full_scan: bool
    periodic_seconds: int
    reconcile_orphans: bool


@dataclass(frozen=True, slots=True)
class AuthConfig:
    token_path: Path | None    # if set, load a shared secret from this file

    def token(self) -> str | None:
        if self.token_path is None:
            return None
        try:
            return self.token_path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None


@dataclass(frozen=True, slots=True)
class Config:
    vault_path: Path
    daemon_bind_host: str
    daemon_bind_port: int
    ollama_url: str
    embedding_model: str
    embedding_dimension: int
    chunk_max_chars: int
    gitea: GiteaConfig
    search: SearchConfig
    scan: ScanConfig
    auth: AuthConfig


def load_config(path: Path) -> Config:
    """Load a vault-writer config YAML file into a typed Config.

    Raises FileNotFoundError if missing, ValueError on malformed fields.
    """
    if not path.exists():
        raise FileNotFoundError(path)

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"config file must be a YAML mapping: {path}")

    vault_path = Path(str(raw["vault_path"]))
    if not vault_path.is_dir():
        raise ValueError(f"vault_path does not exist or is not a directory: {vault_path}")

    host = str(raw.get("daemon_bind_host", "127.0.0.1"))
    if not (host.startswith("127.") or host == "localhost" or host == "::1"):
        raise ValueError(
            f"daemon_bind_host must be loopback (127.*, ::1, localhost); got {host!r}"
        )

    port = int(raw.get("daemon_bind_port", 8765))
    if not (0 <= port <= 65535):
        raise ValueError(f"daemon_bind_port must be 0-65535, got {port}")

    gitea_raw = raw.get("gitea") or {}
    search_raw = raw.get("search") or {}
    scan_raw = raw.get("scan") or {}
    auth_raw = raw.get("auth") or {}

    token_path_raw = auth_raw.get("token_path")
    token_path: Path | None = Path(str(token_path_raw)) if token_path_raw else None

    chunk_max_chars = int(raw.get("chunk_max_chars", 4000))
    if chunk_max_chars < 256:
        raise ValueError(
            f"chunk_max_chars must be at least 256, got {chunk_max_chars}"
        )

    return Config(
        vault_path=vault_path,
        daemon_bind_host=host,
        daemon_bind_port=port,
        ollama_url=str(raw.get("ollama_url", "http://localhost:11434")),
        embedding_model=str(raw.get("embedding_model", "nomic-embed-text")),
        embedding_dimension=int(raw.get("embedding_dimension", 768)),
        chunk_max_chars=chunk_max_chars,
        gitea=GiteaConfig(
            remote=str(gitea_raw.get("remote", "")),
            token_env=str(gitea_raw.get("token_env", "GITEA_TOKEN")),
            push_on_write=bool(gitea_raw.get("push_on_write", False)),
            batch_window_seconds=int(gitea_raw.get("batch_window_seconds", 5)),
        ),
        search=SearchConfig(
            default_k=int(search_raw.get("default_k", 5)),
            min_score=float(search_raw.get("min_score", 0.4)),
        ),
        scan=ScanConfig(
            initial_full_scan=bool(scan_raw.get("initial_full_scan", True)),
            periodic_seconds=int(scan_raw.get("periodic_seconds", 300)),
            reconcile_orphans=bool(scan_raw.get("reconcile_orphans", True)),
        ),
        auth=AuthConfig(token_path=token_path),
    )
