"""Scout daemon configuration."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

load_dotenv(dotenv_path=str(_PROJECT_ROOT / "config" / ".env"))

# Paths
PROJECT_DIR = _PROJECT_ROOT
SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
LOG_DIR = Path(r"C:\tmp\ai-team")
TERRY_LOG = LOG_DIR / "terry.log"
GATEWAY_LOG = LOG_DIR / "gateway.log"
DAEMON_LOG = LOG_DIR / "scout-daemon.log"
CONTEXT_FILE = _PROJECT_ROOT / "memory" / "scout-context.json"

# GPU assignment (informational; not enforced here)
GAMING_GPU = 0  # RTX 4080 — reserved for games
AI_GPUS = [1, 2]  # RTX 5060 Ti x2

# Monitoring intervals (seconds)
HEALTH_CHECK_INTERVAL = 60
GPU_CHECK_INTERVAL = 30
WATCHDOG_INTERVAL = 45

# Thresholds
GPU_TEMP_WARN = 85  # C
GPU_TEMP_CRITICAL = 92
GPU_VRAM_WARN_PCT = 95  # percent
DISK_WARN_GB = 20  # warn below this free space

# RPC server
RPC_HOST = "127.0.0.1"
RPC_PORT = 8767

# ntfy
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "ai-team-alerts")
NTFY_URL = os.environ.get("NTFY_URL", "https://ntfy.sh")

# Gateway supervisor: auto-restart the gateway on consecutive health failures.
# Set SCOUT_GATEWAY_AUTORESTART=false in config/.env to disable.
_autorestart_raw = os.environ.get("SCOUT_GATEWAY_AUTORESTART", "true").lower()
GATEWAY_AUTORESTART: bool = _autorestart_raw not in ("0", "false", "no", "off")

# Proactive Hive: when enabled, scout posts a synthetic trigger to the
# gateway's /v1/proactive/trigger endpoint on new alert conditions.
# DEFAULT OFF — zero behavior change until explicitly enabled.
# Set SCOUT_PROACTIVE_HIVE_ENABLED=true in config/.env to activate.
_proactive_raw = os.environ.get("SCOUT_PROACTIVE_HIVE_ENABLED", "false").lower()
PROACTIVE_HIVE_ENABLED: bool = _proactive_raw in ("1", "true", "yes", "on")
# Gateway base URL for the proactive endpoint. Defaults to localhost.
GATEWAY_URL: str = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8000")
# Gateway auth token for internal API calls. Reads GATEWAY_AUTH_TOKEN from env.
GATEWAY_AUTH_TOKEN: str = os.environ.get("GATEWAY_AUTH_TOKEN", "")
