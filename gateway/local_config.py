"""
Local dev config — unified env var loader for Ai-Team gateway.

Replaces scattered os.environ.get() calls across the codebase with a single
get_config() function that reads from:
  1. os.environ (highest precedence)
  2. ~/.hive/config/secrets.env file
  3. defaults in code (safe for public mirror / dev mode)

NEVER commits real credentials to git or its history.
Public mirror runs with zero secrets — uses dev defaults only.

Usage:
    from local_config import get_config, required_env
    api_key = get_config("CLAUDE_API_KEY", default=None)
    vault_url = required_env("GATEWAY_VAULT_URL")  # raises if not set
"""

import os
from pathlib import Path
from typing import Optional


class RequiredEnvMissing(Exception):
    """Raised when a required env var is not set."""
    pass


_HIVE_CONFIG_PATH = Path.home() / ".hive" / "config" / "secrets.env"


def get_config(key: str, default=None) -> Optional[str]:
    """Get a config value — checks env → ~/.hive/config/secrets.env → default."""
    # Priority 1: environment variable (highest precedence)
    val = os.environ.get(key)
    if val is not None:
        return val

    # Priority 2: ~/.hive/config/secrets.env file
    if _HIVE_CONFIG_PATH.exists():
        try:
            secrets = _load_secrets_file(_HIVE_CONFIG_PATH)
            if key in secrets:
                return secrets[key]
        except (PermissionError, OSError):
            pass

    # Priority 3: default
    return default


def required_env(key: str) -> str:
    """Get a required env var — raises if not set."""
    val = os.environ.get(key)
    if val is None:
        raise RequiredEnvMissing(
            f"Required env var '{key}' not set. "
            f"Set it in your environment or add to ~/.hive/config/secrets.env"
        )
    return val


def _load_secrets_file(path: Path) -> dict[str, str]:
    """Load a flat secrets.env file into a dict."""
    if not path.exists():
        return {}

    secrets = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                secrets[key.strip()] = value.strip().strip('"').strip("'")

    return secrets


__all__ = ["get_config", "required_env"]
