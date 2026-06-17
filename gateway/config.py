"""Typed config loader for the gateway."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class VaultWriterConfig:
    host: str
    port: int
    token_path: Path


@dataclass(frozen=True, slots=True)
class PairingConfig:
    code_ttl_seconds: int
    code_length: int
    token_bytes: int


@dataclass(frozen=True, slots=True)
class NodeRegistryConfig:
    db_filename: str = "hive_nodes.db"
    invite_ttl_seconds: int = 600
    heartbeat_offline_seconds: int = 60
    token_bytes: int = 32


@dataclass(frozen=True, slots=True)
class JobDispatchConfig:
    db_filename: str = "hive_jobs.db"
    long_poll_timeout_s: int = 30
    requeue_after_misses: int = 3
    dispatch_timeout_default_s: int = 300
    offline_sweep_interval_s: int = 15
    poll_rate_per_minute: int = 240    # 4 polls/sec/node max
    poll_rate_burst: int = 60


@dataclass(frozen=True, slots=True)
class NtfyConfig:
    base_url: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class RateLimits:
    writes_per_minute: int
    images_per_hour: int


@dataclass(frozen=True, slots=True)
class ImagesConfig:
    auto_lora: bool
    max_auto_loras: int
    image_app_root: Path | None


@dataclass(frozen=True, slots=True)
class TuningConfig:
    """Tunable interval/timeout/limit constants. Previously scattered as
    module-level `_FOO_S = 30` literals across calendar_jobs / hive_coordinator
    / image_build_state / conversation_memory / turn_log.

    Override per-deployment via `gateway.yaml` if needed; defaults match
    the values shipped in the audit-fix sweep.
    """
    # Calendar
    calendar_tick_s: int = 30
    calendar_per_job_timeout_s: int = 180
    # Image build state
    image_build_inactivity_s: int = 30 * 60
    # Memory
    summary_refresh_every_turns: int = 5
    # Turn log
    turn_log_preview_chars: int = 500
    # Hive turn budget
    hive_turn_total_s: float = 150.0
    hive_synth_reservation_s: float = 60.0
    hive_vram_budget_mb: int = 14000
    hive_min_free_ram_mb: int = 4000


@dataclass(frozen=True, slots=True)
class Config:
    bind_host: str
    bind_port: int
    tailscale_bind: str | None
    state_dir: Path
    vault_writer: VaultWriterConfig
    vault_path: Path
    history_roots: dict[str, Path] = field(default_factory=dict)
    models: dict[str, str | None] = field(default_factory=dict)
    pairing: PairingConfig = field(
        default_factory=lambda: PairingConfig(300, 8, 32)
    )
    ntfy: NtfyConfig = field(
        default_factory=lambda: NtfyConfig("http://127.0.0.1:8080", False)
    )
    rate_limits: RateLimits = field(
        default_factory=lambda: RateLimits(60, 30)
    )
    images: ImagesConfig = field(
        default_factory=lambda: ImagesConfig(
            auto_lora=True, max_auto_loras=3, image_app_root=None,
        )
    )
    tuning: TuningConfig = field(default_factory=TuningConfig)
    nodes: NodeRegistryConfig = field(default_factory=NodeRegistryConfig)
    jobs: JobDispatchConfig = field(default_factory=JobDispatchConfig)
    # Phase 3.5 — when on, ActionExecutor wires an
    # EntityContradictionDetector that embeds prior vs. new
    # `compiled_truth` and writes a journal entry on a divergent edit.
    # Off by default because each edit costs ~2 embed round-trips.
    feature_contradiction_detection: bool = False
    # Optional LLM re-rank step for user-initiated hybrid search.
    # When on, the top-20 RRF candidates from search_chat /
    # search_notes are sent to the cheap LLM for semantic re-ordering.
    # Adds ~1 s of latency, so off by default (opt-in per deployment).
    # Never applies to chat_recall (turn-time path — latency matters).
    feature_search_llm_rerank: bool = False
    # #472: when true, gateway sends SIGTERM to itself if the boot probe
    # finds planner-qwen on CPU (or partially offloaded). Operator sees a
    # CRITICAL log + clean exit instead of a gateway serving 90s helper
    # timeouts. Default ON — matches #438's stated intent. Override to
    # false in gateway.yaml only when intentionally testing CPU-mode.
    ollama_probe_abort_on_bad_verdict: bool = True
    # #473: mid-run watchdog re-runs the residency probe on a slow tick
    # to catch drift after boot (Ollama service restart, VRAM-overflow
    # eviction). Same SIGTERM policy as boot probe; share the
    # ollama_probe_abort_on_bad_verdict gate. Set interval to 0 to
    # disable the watchdog entirely.
    ollama_watchdog_interval_s: float = 300.0
    # Maximum estimated USD to spend on hive→claude-code escalations in
    # a rolling 24-hour window. Tasks that would escalate when the budget
    # is exhausted are parked in review with an explanatory comment instead.
    # Set to 0.0 or None (via gateway.yaml) to disable the cap.
    crew_escalation_daily_usd_cap: float | None = 20.0
    # ---- hive-lite middle rung (plumbing only — default OFF) -----------
    # When True, a cheaper one-card model sits between hive and
    # claude-code in the escalation ladder ("hive" → "hive-lite" →
    # "claude-code").  The model to use is crew_hive_lite_model.
    # Neither key changes any existing behaviour while both are at their
    # defaults (False / None).  Set crew_hive_lite_enabled=true in
    # gateway.yaml to activate once a suitable model is bench-validated.
    crew_hive_lite_enabled: bool = False
    crew_hive_lite_model: str | None = None
    # Max concurrent hive tasks per assignee on a parallel=True project.
    # Default 1 (unchanged).  Reads crew_parallel_lane_cap from YAML so
    # the cap can be raised to 2 once a one-card lane model is wired.
    crew_parallel_lane_cap: int = 1
    # Auto-archive done tasks whose last update is older than this many days
    # (keeps the Done column from growing without bound). 0 or negative = never
    # auto-archive. The dispatcher reaper sweeps periodically.
    crew_done_retention_days: float = 3.0
    # Suno music library paths. Override in gateway.yaml if the library lives
    # elsewhere. Routes return an empty list / 503 if the db/dir is missing.
    suno_library_db: str = ""
    suno_downloads_dir: str = ""
    # Operator app store. APKs + catalog JSON live under the
    # persistent state/ dir so they survive a gateway restart. The publish
    # skill POSTs APKs here from the local machine; the phone GETs the catalog
    # + apk over the configured base URL. appstore_public_base_url is baked into
    # each entry's apkUrl so the phone can reach the gateway.
    appstore_apk_dir: str = ""
    appstore_catalog_path: str = ""
    appstore_public_base_url: str = "http://127.0.0.1:8766"
    # Terminal PTY-over-WS. SECURITY: the WS endpoint enforces loopback-only
    # AND Bearer auth — but an operator can disable it entirely here.
    terminal_enabled: bool = True
    # Max concurrent terminal sessions (to cap orphan shells). The dashboard's
    # multi-session terminal holds one live WS per open tab, so this is also the
    # tab ceiling. 8 lets an operator juggle several shells; the idle reaper
    # (terminal_idle_timeout_s) still kills truly-idle ones.
    terminal_max_sessions: int = 8
    # Idle timeout: kill the shell after this many seconds with no stdin.
    terminal_idle_timeout_s: float = 600.0


def _is_loopback_or_tailscale(host: str) -> bool:
    """True if host is loopback or clearly a Tailscale address.

    Tailscale IPs are in 100.64.0.0/10; hostname form (e.g. "my-pc") is
    also allowed because Tailscale resolves it. 0.0.0.0 and public IPs
    are rejected to prevent accidental LAN/WAN exposure.
    """
    if host in ("127.0.0.1", "::1", "localhost"):
        return True
    if host.startswith("127."):
        return True
    # Tailscale CGNAT range 100.64.0.0/10.
    if host.startswith("100."):
        try:
            parts = [int(p) for p in host.split(".")]
            if len(parts) == 4 and 64 <= parts[1] <= 127:
                return True
        except ValueError:
            pass
    # Non-dotted host (bare hostname) — assume tailnet-resolved.
    if "." not in host:
        return True
    return False


def load_config(path: Path) -> Config:
    """Load gateway.yaml into a typed Config. Raises ValueError on bad values."""
    if not path.exists():
        raise FileNotFoundError(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"config must be a YAML mapping: {path}")

    bind_host = str(raw.get("bind_host", "127.0.0.1"))
    if not _is_loopback_or_tailscale(bind_host):
        raise ValueError(
            f"bind_host must be loopback or Tailscale (not public): {bind_host!r}"
        )

    bind_port = int(raw.get("bind_port", 8766))
    if not (0 < bind_port <= 65535):
        raise ValueError(f"bind_port out of range: {bind_port}")

    tailscale_bind = raw.get("tailscale_bind")
    if tailscale_bind is not None:
        tailscale_bind = str(tailscale_bind)
        if not _is_loopback_or_tailscale(tailscale_bind):
            raise ValueError(
                f"tailscale_bind must be loopback or Tailscale: {tailscale_bind!r}"
            )

    state_dir = Path(str(raw.get("state_dir")))

    vw_raw = raw.get("vault_writer") or {}
    vault_writer = VaultWriterConfig(
        host=str(vw_raw.get("host", "127.0.0.1")),
        port=int(vw_raw.get("port", 8765)),
        token_path=Path(str(vw_raw.get("token_path"))),
    )

    vault_path = Path(str(raw.get("vault_path")))

    history_roots_raw = raw.get("history_roots") or {}
    history_roots = {k: Path(str(v)) for k, v in history_roots_raw.items()}

    models_raw = raw.get("models") or {}
    models = {k: (None if v in (None, "") else str(v)) for k, v in models_raw.items()}

    p = raw.get("pairing") or {}
    pairing = PairingConfig(
        code_ttl_seconds=int(p.get("code_ttl_seconds", 300)),
        code_length=int(p.get("code_length", 8)),
        token_bytes=int(p.get("token_bytes", 32)),
    )

    n = raw.get("ntfy") or {}
    ntfy = NtfyConfig(
        base_url=str(n.get("base_url", "http://127.0.0.1:8080")),
        enabled=bool(n.get("enabled", False)),
    )

    rl = raw.get("rate_limits") or {}
    rate_limits = RateLimits(
        writes_per_minute=int(rl.get("writes_per_minute", 60)),
        images_per_hour=int(rl.get("images_per_hour", 30)),
    )

    im = raw.get("images") or {}
    _app_root = im.get("image_app_root")
    images = ImagesConfig(
        auto_lora=bool(im.get("auto_lora", True)),
        max_auto_loras=int(im.get("max_auto_loras", 3)),
        image_app_root=Path(str(_app_root)) if _app_root else None,
    )

    nodes_raw = raw.get("nodes") or {}
    nodes = NodeRegistryConfig(
        db_filename=str(nodes_raw.get("db_filename", "hive_nodes.db")),
        invite_ttl_seconds=int(nodes_raw.get("invite_ttl_seconds", 600)),
        heartbeat_offline_seconds=int(
            nodes_raw.get("heartbeat_offline_seconds", 60)
        ),
        token_bytes=int(nodes_raw.get("token_bytes", 32)),
    )

    jobs_raw = raw.get("jobs") or {}
    jobs = JobDispatchConfig(
        db_filename=str(jobs_raw.get("db_filename", "hive_jobs.db")),
        long_poll_timeout_s=int(jobs_raw.get("long_poll_timeout_s", 30)),
        requeue_after_misses=int(jobs_raw.get("requeue_after_misses", 3)),
        dispatch_timeout_default_s=int(
            jobs_raw.get("dispatch_timeout_default_s", 300),
        ),
        offline_sweep_interval_s=int(
            jobs_raw.get("offline_sweep_interval_s", 15),
        ),
        poll_rate_per_minute=int(jobs_raw.get("poll_rate_per_minute", 240)),
        poll_rate_burst=int(jobs_raw.get("poll_rate_burst", 60)),
    )

    feature_contradiction_detection = bool(
        raw.get("feature_contradiction_detection", False)
    )

    feature_search_llm_rerank = bool(
        raw.get("feature_search_llm_rerank", False)
    )

    ollama_probe_abort_on_bad_verdict = bool(
        raw.get("ollama_probe_abort_on_bad_verdict", True)
    )

    ollama_watchdog_interval_s = float(
        raw.get("ollama_watchdog_interval_s", 300.0)
    )

    _cap_raw = raw.get("crew_escalation_daily_usd_cap", 20.0)
    crew_escalation_daily_usd_cap: float | None = (
        None if _cap_raw in (None, 0, 0.0) else float(_cap_raw)
    )

    crew_hive_lite_enabled = bool(raw.get("crew_hive_lite_enabled", False))
    _lite_model_raw = raw.get("crew_hive_lite_model")
    crew_hive_lite_model: str | None = (
        str(_lite_model_raw) if _lite_model_raw not in (None, "") else None
    )
    crew_parallel_lane_cap = int(raw.get("crew_parallel_lane_cap", 1))
    crew_done_retention_days = float(raw.get("crew_done_retention_days", 3.0))

    terminal_enabled = bool(raw.get("terminal_enabled", True))
    terminal_max_sessions = int(raw.get("terminal_max_sessions", 8))
    terminal_idle_timeout_s = float(raw.get("terminal_idle_timeout_s", 600.0))

    appstore_apk_dir = str(
        raw.get("appstore_apk_dir", os.environ.get("HIVE_APPSTORE_APK_DIR", ""))
    )
    appstore_catalog_path = str(
        raw.get(
            "appstore_catalog_path",
            os.environ.get("HIVE_APPSTORE_CATALOG_PATH", ""),
        )
    )
    appstore_public_base_url = str(
        raw.get(
            "appstore_public_base_url",
            os.environ.get("HIVE_PUBLIC_BASE_URL", "http://127.0.0.1:8766"),
        )
    ).rstrip("/")

    return Config(
        bind_host=bind_host,
        bind_port=bind_port,
        tailscale_bind=tailscale_bind,
        state_dir=state_dir,
        vault_writer=vault_writer,
        vault_path=vault_path,
        history_roots=history_roots,
        models=models,
        pairing=pairing,
        ntfy=ntfy,
        rate_limits=rate_limits,
        images=images,
        nodes=nodes,
        jobs=jobs,
        feature_contradiction_detection=feature_contradiction_detection,
        feature_search_llm_rerank=feature_search_llm_rerank,
        ollama_probe_abort_on_bad_verdict=ollama_probe_abort_on_bad_verdict,
        ollama_watchdog_interval_s=ollama_watchdog_interval_s,
        crew_escalation_daily_usd_cap=crew_escalation_daily_usd_cap,
        crew_hive_lite_enabled=crew_hive_lite_enabled,
        crew_hive_lite_model=crew_hive_lite_model,
        crew_parallel_lane_cap=crew_parallel_lane_cap,
        crew_done_retention_days=crew_done_retention_days,
        terminal_enabled=terminal_enabled,
        terminal_max_sessions=terminal_max_sessions,
        terminal_idle_timeout_s=terminal_idle_timeout_s,
        appstore_apk_dir=appstore_apk_dir,
        appstore_catalog_path=appstore_catalog_path,
        appstore_public_base_url=appstore_public_base_url,
    )
