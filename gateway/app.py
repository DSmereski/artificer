"""FastAPI app factory for the ai-team-gateway."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

# Make the Ai-Team package imports resolve regardless of how uvicorn is invoked.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from gateway.auth import DeviceStore, PairingBroker
from gateway.bot_adapters.hive import HiveAdapter
from gateway.claude_code import ClaudeCodeManager
from gateway.config import Config, load_config
from gateway.deps import AppState
from gateway.events import EventBus
from gateway.action_executor import ActionExecutor
from gateway.asset_importer import AssetImportStore
from gateway.calendar_jobs import FireResult, JobStore, Scheduler
from gateway.conversation_memory import MemoryStore
from gateway.helpers.factory import build_helpers
from gateway.hive_coordinator import HiveCoordinator
from gateway.image_build_state import ImageBuildStore
from gateway.avatar_shim import AvatarShim
from gateway.image_shim import ImageJob, ImageShim
from gateway.video_shim import VideoJob, VideoShim
from gateway.model_catalog import ModelCatalog, load_catalog
from gateway.ntfy import NtfyClient
from gateway.recipe_store import RecipeStore
from gateway.escalation_store import EscalationStore
from gateway.skill_registry import SkillRegistry
from gateway.turn_log import TurnLogStore
from gateway.turn_telemetry import TurnTelemetry
from gateway.routes import app_update as app_update_route
from gateway.routes import bots as bots_route
from gateway.routes import chat as chat_route
from gateway.routes import events as events_route
from gateway.routes import hive as hive_route
from gateway.routes import images as images_route
from gateway.routes import models as models_route
from gateway.routes import pair as pair_route
from gateway.routes import calendar as calendar_route
from gateway.routes import config as config_route
from gateway.routes import loras as loras_route
from gateway.routes import recipes as recipes_route
from gateway.routes import escalations as escalations_route
from gateway.routes import skills as skills_route
from gateway.routes import videos as videos_route
from gateway.routes import telemetry as telemetry_route
from gateway.routes import scout as scout_route
from gateway.routes import docker as docker_route
from gateway.routes import gitactivity as gitactivity_route
from gateway.routes import digest as digest_route
from gateway.routes import gpu_mode as gpu_mode_route
from gateway.routes import theme as theme_route
from gateway.routes import search as search_route
from gateway.routes import system as system_route
from gateway.routes import invites as invites_route
from gateway.routes import node_pair as node_pair_route
from gateway.routes import nodes as nodes_route
from gateway.routes import jobs as jobs_route
from gateway.routes import admin as admin_route
from gateway.routes import vault as vault_route
from gateway.routes import graph as graph_route
from gateway.routes import proactive as proactive_route  # connected-brain Item 2
from gateway.routes import voice as voice_route
from gateway.routes import stt as stt_route
from gateway.routes import suno as suno_route
from gateway.routes import music as music_route
from gateway.routes import appstore as appstore_route
from gateway.routes import terminal as terminal_route
from gateway.routes import wiki as wiki_route
from gateway.rate_limit import RateLimiter
from gateway import scout_alerts as scout_alerts_task
from gateway.scout_history import ScoutHistory
from gateway.voice_shim import VoicePipeline
from gateway import image_catalog as image_catalog_mod
from gateway import image_lora_doc
from gateway import vault_smart_link
from gateway.recent_images import RecentImagesStore
from shared.vault_client import VaultClient


log = logging.getLogger("gateway.app")


def _build_adapters(
    config: Config,
    claude_manager: ClaudeCodeManager,
    vault_client: VaultClient | None,
    image_catalog: image_catalog_mod.ImageCatalog | None,
) -> dict[str, object]:
    """Wire every bot adapter referenced in config.history_roots.

    Hive is the sole chat persona. Maggy + Scout were decommissioned
    earlier; Claude Code was removed at the user's request — the
    developer-side claude-code workflow happens outside the app now.
    Legacy bot names redirect to Hive in `routes/chat.py`.
    """
    adapters: dict[str, object] = {}
    hd = config.history_roots.get("hive")
    if hd is not None:
        adapters["hive"] = HiveAdapter(
            history_dir=hd,
            model=config.models.get("hive"),
            vault_client=vault_client,
            image_catalog=image_catalog,
        )
    return adapters


def _make_calendar_fire(*, hive_coordinator, executor, app_state_holder):
    """Return an async fire callable for the calendar Scheduler.

    Each scheduled job is mapped to either a hive turn (action_verb =
    'hive_turn') or a one-shot ActionExecutor verb (everything else).

    `app_state_holder` is a 1-element dict with key "state" that the
    caller fills in once `app.state.ai_team` exists. Calendar-fire
    hive turns get indexed into `chat_log` with thread_id="calendar"
    so they're searchable from the unified search bar.
    """
    async def _fire(job):
        verb = job.action_verb
        if verb == "hive_turn":
            if hive_coordinator is None:
                return FireResult(
                    job_id=job.id, ok=False,
                    detail="hive coordinator not available",
                )
            from gateway.event_emitter import ListEmitter
            from gateway.hive_coordinator import TurnContext
            user_msg = str(job.action_payload.get("user_msg", ""))
            user_id = hash(job.id) & 0xFFFFFFFF
            ctx = TurnContext(
                user_msg=user_msg,
                user_id=user_id,
                device_id=job.owner_device_id or "calendar",
                bot="hive",
                # Pass empty — the coordinator falls back to its
                # registered helpers list when this is empty.
                available_helpers=[],
            )
            try:
                turn = await hive_coordinator.coordinate(ctx, ListEmitter())
            except Exception as e:  # noqa: BLE001
                return FireResult(
                    job_id=job.id, ok=False,
                    detail=f"{type(e).__name__}: {e}",
                )
            # Index the calendar-fire turn into chat_log under the
            # dedicated `calendar` thread so it's searchable later.
            ai_team = app_state_holder.get("state")
            if ai_team is not None:
                try:
                    from gateway.hive_turn_helpers import (
                        index_hive_turn_to_chat_log,
                    )
                    index_hive_turn_to_chat_log(
                        ai_team, turn,
                        user_id=user_id, text=user_msg,
                        bot="hive", thread_id="calendar",
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("calendar chat_log index failed: %s", e)
            return FireResult(
                job_id=job.id,
                ok=turn.error is None and not turn.blocked,
                detail=(turn.reply or turn.error or "")[:300],
            )
        # All other verbs go straight through the executor.
        receipts = await executor.execute_all(
            [{"verb": verb, "payload": job.action_payload}],
            device_id=job.owner_device_id,
        )
        if receipts and receipts[0].ok:
            return FireResult(job_id=job.id, ok=True, detail=receipts[0].detail)
        detail = receipts[0].detail if receipts else "no receipt"
        return FireResult(job_id=job.id, ok=False, detail=detail)
    return _fire


async def _polish_loop(
    catalog,
    build_store,
    *,
    catalog_interval_s: int = 3600,
    build_interval_s: int = 300,
) -> None:
    """Periodic upkeep: refresh model catalog liveness + drop stale image builds."""
    last_catalog = 0.0
    last_build = 0.0
    while True:
        try:
            await asyncio.sleep(min(build_interval_s, 60))
            now = asyncio.get_running_loop().time()
            if catalog is not None and now - last_catalog > catalog_interval_s:
                try:
                    catalog.refresh_from_ollama()
                    last_catalog = now
                except Exception as e:  # noqa: BLE001
                    log.warning("catalog refresh failed: %s", e)
            if build_store is not None and now - last_build > build_interval_s:
                try:
                    n = build_store.cleanup_stale()
                    if n:
                        log.info("dropped %d stale image build(s)", n)
                    last_build = now
                except Exception as e:  # noqa: BLE001
                    log.warning("build cleanup failed: %s", e)
        except asyncio.CancelledError:
            return


async def _prewarm_helper_models(router, *, roles=("planner", "summarizer", "synthesizer")) -> None:
    """Fire one tiny chat per distinct planner-tier Ollama model so the
    weights load into VRAM before the first user turn arrives.

    Without this, the first turn after gateway boot pays a 30-90s cold
    load that consistently blows the 90s planner / 45s summarizer
    timeouts (observed in gateway.log.err 2026-05-01). Combined with
    ``keep_alive=24h`` on every helper call, this means cold-load only
    happens once per gateway lifecycle.

    Best-effort: any failure (router missing, Ollama down, role unknown)
    is logged and swallowed so it never blocks boot.
    """
    if router is None:
        return
    from gateway.helpers.base import OllamaInvoker
    seen: set[str] = set()
    for role in roles:
        try:
            choice = router.route_for(role)
        except Exception as e:  # noqa: BLE001
            log.debug("prewarm: route_for(%s) failed: %s", role, e)
            continue
        model = choice.model.ollama_name
        if not model or model in seen:
            continue
        seen.add(model)
        invoker = OllamaInvoker(timeout=180.0)
        t0 = time.time()
        try:
            await invoker.chat(
                model=model,
                system="warmup",
                user="ok",
                params={"num_predict": 1, "temperature": 0},
            )
            log.info("prewarm: %s loaded in %.1fs", model, time.time() - t0)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "prewarm: %s failed after %.1fs: %s",
                model, time.time() - t0, e,
            )


async def _prewarm_then_probe_hive_qwen(
    router,
    app_state: "AppState",
    *,
    prewarm_roles: tuple[str, ...] = ("planner", "summarizer", "synthesizer"),
    probe_model_prefix: str = "planner-qwen",
    abort_on_bad_verdict: bool = False,
) -> None:
    """Prewarm helper models, then verify planner-qwen is GPU-resident.

    Defense layer 2 against the Ollama tray-autostart env-var-drift
    regression (#437): even with the Startup\\Ollama.lnk shortcut now
    pointing at scripts/start-ollama-tuned.cmd, a future runtime drift
    (#439's hypothesised VRAM-overflow path, or any new misconfig) would
    silently put planner-qwen on CPU and tank every helper turn. The probe
    catches it at boot so the operator sees one CRITICAL log line
    instead of debugging 90s helper timeouts.

    With ``abort_on_bad_verdict=True`` (#472), a definite CPU or mixed
    verdict raises SIGTERM at the gateway process so uvicorn shuts down
    rather than serving in a degraded state. Transient verdicts
    (``unreachable``, ``missing``) only warn — they're often flaky at
    boot while Ollama is still warming.

    Result is stashed on ``app_state.ollama_probe_result`` for future
    /v1/health surfacing.
    """
    await _prewarm_helper_models(router, roles=prewarm_roles)

    try:
        from gateway.ollama_probe import check_model_on_gpu
        result = await check_model_on_gpu(probe_model_prefix)
    except Exception as e:  # noqa: BLE001
        log.warning("ollama probe raised unexpectedly: %s", e)
        return

    app_state.ollama_probe_result = result

    if result.processor == "gpu":
        log.info(
            "ollama probe: %s 100%% GPU-resident (%s)",
            probe_model_prefix, result.model_name or probe_model_prefix,
        )
    elif result.processor == "cpu":
        log.critical(
            "ollama probe: %s is on CPU — Ollama is misconfigured. "
            "Restart via scripts/start-ollama-tuned.cmd. Detail: %s",
            probe_model_prefix, result.message,
        )
        if abort_on_bad_verdict:
            _abort_gateway_for_bad_probe(result.processor, result.message)
    elif result.processor == "mixed":
        log.warning(
            "ollama probe: %s partially offloaded (%.0f%% GPU). "
            "Detail: %s",
            probe_model_prefix, result.gpu_pct, result.message,
        )
        if abort_on_bad_verdict:
            _abort_gateway_for_bad_probe(result.processor, result.message)
    elif result.processor == "missing":
        log.warning(
            "ollama probe: %s not loaded yet (prewarm may have failed). "
            "Detail: %s",
            probe_model_prefix, result.message,
        )
    else:
        log.warning("ollama probe: %s — %s", result.processor, result.message)


def _abort_gateway_for_bad_probe(processor: str, detail: str) -> None:
    """Shut gateway down on definite GPU-residency failure (#472).

    Sends SIGTERM to the current process so uvicorn's signal handler
    drains connections cleanly. Operator sees a CRITICAL log line plus
    a clean exit instead of a half-broken gateway serving 90s helper
    timeouts. Reversible via ``ollama_probe_abort_on_bad_verdict: false``
    in gateway.yaml.
    """
    import signal
    log.critical(
        "ollama probe: aborting gateway (processor=%s) — fix Ollama and "
        "restart. Set ollama_probe_abort_on_bad_verdict=false in "
        "gateway.yaml to disable. Detail: %s",
        processor, detail,
    )
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except OSError as e:
        log.error("failed to send SIGTERM for probe abort: %s", e)


async def _canon_refresh_loop(adapters: dict[str, object], interval_seconds: int = 1800) -> None:
    """Refresh vault canon for every adapter that supports it, every 30 min.

    Mirrors the Discord bots' @tasks.loop(minutes=30) behavior so bots in the
    app pick up canon edits without restarting the gateway.
    """
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise
        for name, adapter in adapters.items():
            refresh = getattr(adapter, "refresh_canon", None)
            if refresh is None:
                continue
            try:
                chars = await refresh()
                log.info("canon refreshed for %s (%d chars)", name, chars)
            except Exception as e:  # noqa: BLE001
                log.warning("canon refresh failed for %s: %s", name, e)


def create_app(config: Config | None = None) -> FastAPI:
    """Build the FastAPI app. Accepts an override config (used by tests)."""
    if config is None:
        cfg_path = Path(os.environ.get(
            "GATEWAY_CONFIG",
            str(_PROJECT_ROOT / "config" / "gateway.yaml"),
        ))
        config = load_config(cfg_path)

    state_dir = config.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)

    devices = DeviceStore(state_dir / "devices.json")
    # GC on startup: drop revoked entries and transient test pairings
    # so the user's paired-device list stays short across restarts.
    # Source of truth for the prefix list is gateway.routes.pair so
    # the startup hook and the /devices/purge endpoint stay in sync.
    try:
        from gateway.routes.pair import _TRANSIENT_DEVICE_PREFIXES
        n_revoked = devices.purge_revoked()
        n_test = devices.purge_by_name_prefix(_TRANSIENT_DEVICE_PREFIXES)
        if n_revoked or n_test:
            log.info(
                "device GC on startup: dropped %d revoked, %d transient",
                n_revoked, n_test,
            )
    except Exception as e:  # noqa: BLE001
        log.warning("device GC failed (continuing): %s", e)
    pairing = PairingBroker(
        ttl_seconds=config.pairing.code_ttl_seconds,
        code_length=config.pairing.code_length,
    )
    from gateway.worker_pool.invites import InviteBroker
    from gateway.worker_pool.registry import NodeRegistry
    from gateway.worker_pool.dispatcher import Dispatcher
    from gateway.worker_pool.scheduler import Scheduler as HiveScheduler
    node_registry = NodeRegistry.open(state_dir / config.nodes.db_filename)
    node_invites = InviteBroker(ttl_seconds=config.nodes.invite_ttl_seconds)
    dispatcher = Dispatcher.open(state_dir / config.jobs.db_filename)
    scheduler = HiveScheduler(dispatcher=dispatcher)
    scout_history = ScoutHistory(state_dir / "scout-history.jsonl")
    event_bus = EventBus()
    # M2.1: model catalog — drives the M2.3 HiveCoordinator and the
    # /v1/models endpoint. Falls back to None if the YAML is missing
    # so existing tests (which don't ship a catalog) still construct.
    # M3: skill registry — single source of truth shared with Claude Code.
    skill_registry: SkillRegistry | None = None
    skills_dir = config.vault_path / "skills"
    if skills_dir.is_dir():
        skill_registry = SkillRegistry(skills_dir)
        n = skill_registry.load()
        log.info("skill registry loaded: %d skills from %s", n, skills_dir)

    # Catalog + helpers + coordinator are constructed AFTER image_shim,
    # build_store, ntfy etc. exist (see below). Declare placeholders.
    model_catalog: ModelCatalog | None = None
    helpers: dict = {}
    hive_coordinator: HiveCoordinator | None = None
    router = None
    catalog_path = _PROJECT_ROOT / "config" / "model_catalog.yaml"

    ntfy = NtfyClient(base_url=config.ntfy.base_url, enabled=config.ntfy.enabled)
    voice_pipeline = VoicePipeline()
    claude_manager = ClaudeCodeManager()
    rate_limiter = RateLimiter()
    rate_limiter.configure(
        writes_per_minute=config.rate_limits.writes_per_minute,
        images_per_hour=config.rate_limits.images_per_hour,
    )
    # Anonymous /v1/pair/node throttle keyed by client IP. Legitimate
    # pairing is a one-shot per node so the burst is small; this caps
    # brute-force enumeration of the 10^6 invite codespace.
    rate_limiter.register("pair_attempts", per_minute=10, burst=5)

    # Captured at lifespan startup (line below the FastAPI() construction).
    # `_image_done` runs on the image shim's worker thread, so it can't
    # call `get_running_loop()` itself.
    main_loop_holder: dict[str, asyncio.AbstractEventLoop] = {}

    def _image_done(job: ImageJob) -> None:
        event_bus.publish({
            "type": "image_done",
            "job_id": job.id,
            "state": job.state,
            "result_ids": list(job.result_ids),
            "error": job.error,
        })
        if not (ntfy.enabled and job.state == "done"):
            return
        loop = main_loop_holder.get("loop")
        if loop is None:
            return
        # Schedule the async ntfy publish on the gateway loop thread.
        coro_factory = lambda: ntfy.publish(   # noqa: E731
            topic="ai-team-image",
            title="Image ready",
            message=f"{len(job.result_ids)} image(s) finished rendering",
            tags=["frame_photo"],
        )
        loop.call_soon_threadsafe(
            lambda: loop.create_task(coro_factory()),
        )

    image_shim = ImageShim(state_dir / "media", on_done=_image_done)

    def _video_done(job: VideoJob) -> None:
        event_bus.publish({
            "type": "video_done",
            "job_id": job.id,
            "state": job.state,
            "result_id": job.result_id,
            "error": job.error,
        })
        if not (ntfy.enabled and job.state == "done"):
            return
        loop = main_loop_holder.get("loop")
        if loop is None:
            return
        coro_factory = lambda: ntfy.publish(   # noqa: E731
            topic="ai-team-video",
            title="Video ready",
            message=f"WAN video finished ({job.duration_s:.1f}s)",
            tags=["movie_camera"],
        )
        loop.call_soon_threadsafe(
            lambda: loop.create_task(coro_factory()),
        )

    video_shim = VideoShim(state_dir / "media", on_done=_video_done)
    # Talking-head avatar content: kokoro TTS -> SadTalker. Shares the media
    # dir so .mp4 output lands in the same gallery. The dispatcher polls the
    # job, so no on_done callback is needed for the board path.
    avatar_shim = AvatarShim(state_dir / "media")
    # Persisted ledger so a gateway restart doesn't strand finished
    # images. The user reported "I was never sent an image" on
    # 2026-04-28 right after a dev-cycle gateway bounce — the
    # in-memory ledger had lost the just-rendered job and the app's
    # reconnect-replay returned 0.
    recent_images = RecentImagesStore(path=state_dir / "recent-images.jsonl")
    try:
        n = recent_images.load()
        if n:
            log.info("recent_images: loaded %d persisted jobs", n)
    except Exception as e:  # noqa: BLE001
        log.warning("recent_images load failed (continuing): %s", e)
    # M5.1 + M5.2 + M6.3 stores
    image_build_store = ImageBuildStore(state_dir / "image_builds")
    turn_telemetry = TurnTelemetry(max_records=100)
    memory_store_hive = MemoryStore(state_dir / "memory" / "hive", bot="hive")
    turn_log_store = TurnLogStore(state_dir / "turn-logs", mem_cap=200)
    calendar_store = JobStore(state_dir / "calendar.db")
    asset_import_store = AssetImportStore(
        max_records=50,
        path=state_dir / "asset_imports.json",
    )
    recipe_store = RecipeStore(config.vault_path)
    escalation_store = EscalationStore(config.vault_path)

    # M2.1 + M6: model catalog, helpers, action executor, coordinator.
    # Built after image_shim / build_store / ntfy so the executor can
    # bind to live infra.
    if catalog_path.is_file():
        try:
            model_catalog = load_catalog(catalog_path)
            # Wire durable per-role overrides BEFORE build_helpers so
            # the pool is constructed with the user-chosen models.
            model_catalog.attach_overrides_file(state_dir / "helper_overrides.json")
            report = model_catalog.refresh_from_ollama()
            if report.missing:
                log.warning(
                    "model catalog: %d models missing — %s",
                    len(report.missing), ", ".join(report.missing),
                )
            # Vault factory closure for the Librarian helper to do
            # preflight vault search before invoking its LLM.
            def _librarian_vault_factory():
                from shared.vault_client import VaultClient as _VC
                return _VC(
                    vault_path=config.vault_path,
                    daemon_host=config.vault_writer.host,
                    daemon_port=config.vault_writer.port,
                )

            # Build the Router first so build_helpers can consult it
            # and bake per-role model picks into each helper. Without
            # this the catalog YAML default is wired in even when the
            # bench results say a different model is better for the
            # role (the router was only being consulted for telemetry).
            from gateway.orchestrator.bench_results import load_results
            from gateway.orchestrator.router import Router as _Router

            bench_results_path = state_dir / "bench_results.json"
            router = _Router(
                catalog=model_catalog,
                results=load_results(bench_results_path),
            )

            helpers = build_helpers(
                model_catalog,
                skill_registry=skill_registry,
                vault_client_factory=_librarian_vault_factory,
                router=router,
            )

            def _vault_client_factory():
                from shared.vault_client import VaultClient
                return VaultClient(
                    vault_path=config.vault_path,
                    daemon_host=config.vault_writer.host,
                    daemon_port=config.vault_writer.port,
                )
            # Phase 3: contradiction detector lives behind a config flag
            # so the entity_page_update verb stays cheap by default. The
            # detector is plain-Python — no LLM call — so loading it is
            # free; we just don't wire it through unless asked.
            contradiction_detector = None
            if getattr(config, "feature_contradiction_detection", False):
                from gateway.contradiction_detector import (
                    EntityContradictionDetector,
                )

                def _vc_for_detector():
                    from shared.vault_client import VaultClient
                    return VaultClient(
                        vault_path=config.vault_path,
                        daemon_host=config.vault_writer.host,
                        daemon_port=config.vault_writer.port,
                    )

                contradiction_detector = EntityContradictionDetector(
                    vault_client_factory=_vc_for_detector,
                )
            # Composio (Phase B): optional SaaS bridge. Constructor is
            # total — if neither SDK nor key is present the client returns
            # `composio_unavailable` from execute() so saas_call receipts
            # surface a graceful explanation instead of crashing.
            from gateway.composio.client import ComposioClient
            composio_client = ComposioClient()
            executor = ActionExecutor(
                vault_client_factory=_vault_client_factory,
                image_shim=image_shim,
                video_shim=video_shim,  # connected-brain Item 3: line 607
                ntfy=ntfy,
                skill_registry=skill_registry,
                image_build_store=image_build_store,
                critic_helper=helpers.get("critic"),
                rate_limiter=rate_limiter,
                vault_path=config.vault_path,
                state_dir=state_dir,
                memory_store=memory_store_hive,
                contradiction_detector=contradiction_detector,
                composio_client=composio_client,
            )
            hive_coordinator = HiveCoordinator(
                model_catalog, helpers, executor=executor, router=router,
            )
            log.info("hive coordinator built with %d helpers", len(helpers))
        except Exception as e:  # noqa: BLE001
            log.warning("failed to load model catalog: %s", e)
            model_catalog = None
            helpers = {}
            hive_coordinator = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        vault_client = VaultClient(
            vault_path=config.vault_path,
            daemon_host=config.vault_writer.host,
            daemon_port=config.vault_writer.port,
        )
        catalog = image_catalog_mod.load_catalog(config.images.image_app_root)
        log.info(
            "image catalog loaded (%d loras, %d presets)",
            len(catalog.loras), len(catalog.presets),
        )
        # Regenerate canon/imagegen-loras.md if the on-disk LoRA registry
        # has been updated since the last write. Idempotent + fail-safe.
        if config.images.image_app_root:
            registry_path = config.images.image_app_root / "models" / "loras" / "lora_registry.json"
            # Knowledge/ (not canon/) — catalog is reference data, queried via
            # vault search at request time, NOT spliced into Hive's prompt.
            canon_path = config.vault_path / "knowledge" / "imagegen-loras.md"
            rewrote, n = image_lora_doc.regenerate_if_stale(
                registry_path=registry_path,
                canon_path=canon_path,
            )
            if rewrote:
                log.info("regenerated %s (%d LoRAs)", canon_path, n)

        # Smart-link pass: rewrite proper nouns to [[wikilinks]] across canon +
        # knowledge notes. Best-effort + idempotent, and the smart-link daemon
        # re-runs it every 600s — so do NOT block startup on it. Run it in a
        # worker thread; a slow/hung vault walk (cold disk + vault-writer
        # contention at boot) must never stall the lifespan and keep the gateway
        # from reaching serve() (the cold-boot hang that needed a manual restart).
        try:
            _smartlink_task = asyncio.create_task(
                asyncio.to_thread(vault_smart_link.run, config.vault_path),
                name="smart-link-boot",
            )
            _smartlink_task.add_done_callback(
                lambda t: (not t.cancelled() and t.exception())
                and log.warning("smart-link pass failed: %s", t.exception()))
        except Exception as e:  # noqa: BLE001
            log.warning("smart-link pass kickoff failed: %s", e)
        adapters = _build_adapters(config, claude_manager, vault_client, catalog)
        app.state.ai_team = AppState(
            config=config,
            devices=devices,
            pairing=pairing,
            adapters=adapters,
            scout_history=scout_history,
            image_shim=image_shim,
            video_shim=video_shim,
            event_bus=event_bus,
            ntfy=ntfy,
            voice_pipeline=voice_pipeline,
            claude_code_manager=claude_manager,
            rate_limiter=rate_limiter,
            image_catalog=catalog,
            recent_images=recent_images,
            model_catalog=model_catalog,
            helpers=helpers,
            hive_coordinator=hive_coordinator,
            router=router,
            skill_registry=skill_registry,
            turn_telemetry=turn_telemetry,
            image_build_store=image_build_store,
            memory_store_hive=memory_store_hive,
            turn_log_store=turn_log_store,
            calendar_store=calendar_store,
            asset_import_store=asset_import_store,
            recipe_store=recipe_store,
            escalation_store=escalation_store,
            vault_client=vault_client,
            node_registry=node_registry,
            node_invites=node_invites,
            dispatcher=dispatcher,
            scheduler=scheduler,
        )
        log.info("gateway ready with bots: %s", ", ".join(adapters))

        # Capture the running event loop so the sync `_image_done`
        # callback (called from the image-shim worker thread) has a
        # known target for `call_soon_threadsafe`.
        # Auditor — scans turn-logs hourly, writes findings to vault.
        auditor_scheduler = None
        try:
            from gateway.auditor.scheduler import AuditorScheduler
            auditor_scheduler = AuditorScheduler(
                state_dir=state_dir,
                vault=vault_client,
                bots=["hive", "maggy", "scout"],
            )
            auditor_task = auditor_scheduler.start()
            from gateway.deps import track_background_task as _track
            _track(app.state.ai_team, auditor_task)
            app.state.ai_team.auditor_scheduler = auditor_scheduler
        except Exception:  # noqa: BLE001
            log.exception("auditor scheduler failed to start")
            auditor_scheduler = None

        groomer_idle_loop = None
        try:
            from vault_writer.groomer.idle_loop import IdleGroomerLoop
            # Honour a 5-minute grace period on boot: treat startup as if a
            # user turn just completed so the groomer can't fire immediately.
            app.state.ai_team.last_turn_completed_at = time.time()
            groomer_idle_loop = IdleGroomerLoop(
                vault_path=config.vault_path,
                app_state=app.state.ai_team,
                # Defense in depth: real conversational rhythm leaves
                # 4-6min gaps; 30min keeps the groomer well clear of
                # mid-conversation runs. Original CPU-eviction theory
                # blamed groomer competition for planner-qwen, but the
                # real cause turned out to be Ollama tray autostart
                # missing CUDA_VISIBLE_DEVICES=1,2 (#437, #438).
                idle_threshold_s=1800.0,
            )
            groomer_task = groomer_idle_loop.start()
            from gateway.deps import track_background_task as _track
            _track(app.state.ai_team, groomer_task)
            app.state.ai_team.groomer_idle_loop = groomer_idle_loop
        except Exception:  # noqa: BLE001
            log.exception("groomer idle loop failed to start")
            groomer_idle_loop = None

        # Pre-warm helper models — fires one tiny chat at each distinct
        # Ollama model used by planner/summarizer/synthesizer roles so
        # the first real user turn doesn't pay the 30-90s cold-load
        # tax that blows helper timeouts. Combined with the keep_alive
        # 24h pin on every helper call (gateway/helpers/base.py),
        # cold-load happens at most once per gateway lifecycle.
        try:
            prewarm_task = asyncio.create_task(
                _prewarm_then_probe_hive_qwen(
                    router, app.state.ai_team,
                    abort_on_bad_verdict=config.ollama_probe_abort_on_bad_verdict,
                ),
                name="prewarm-and-probe",
            )
            from gateway.deps import track_background_task as _track
            _track(app.state.ai_team, prewarm_task)
        except Exception:  # noqa: BLE001
            log.exception("prewarm+probe task failed to start")

        # #473: mid-run watchdog — re-runs the residency probe every
        # ollama_watchdog_interval_s to catch drift after boot. Same
        # SIGTERM policy as the boot probe; share the abort gate.
        # Disable by setting interval to 0.
        if config.ollama_watchdog_interval_s > 0:
            try:
                from gateway.ollama_watchdog import watchdog_loop
                watchdog_task = asyncio.create_task(
                    watchdog_loop(
                        app.state.ai_team,
                        interval_s=config.ollama_watchdog_interval_s,
                        abort_on_bad_verdict=(
                            config.ollama_probe_abort_on_bad_verdict
                        ),
                    ),
                    name="ollama-watchdog",
                )
                from gateway.deps import track_background_task as _track
                _track(app.state.ai_team, watchdog_task)
            except Exception:  # noqa: BLE001
                log.exception("ollama watchdog failed to start")

        main_loop_holder["loop"] = asyncio.get_running_loop()

        alerts_task: asyncio.Task | None = None
        canon_task: asyncio.Task | None = None
        recent_task: asyncio.Task | None = None
        polish_task: asyncio.Task | None = None
        scheduler_task: asyncio.Task | None = None
        try:
            alerts_task = asyncio.create_task(
                scout_alerts_task.run(app.state.ai_team),
                name="scout-alerts",
            )
            canon_task = asyncio.create_task(
                _canon_refresh_loop(adapters),
                name="canon-refresh",
            )
            recent_task = recent_images.attach_to_bus(event_bus)

            # M6 polish: periodic catalog refresh + image-build cleanup.
            if model_catalog is not None:
                polish_task = asyncio.create_task(
                    _polish_loop(model_catalog, image_build_store),
                    name="polish-loop",
                )

            # Calendar scheduler — fires due jobs every 30s.
            # Reuse the executor already wired to the hive (was a
            # second standalone instance until the audit caught the
            # duplication).
            calendar_executor = (
                hive_coordinator.executor if hive_coordinator else None
            )
            if calendar_executor is None:
                # Hive disabled — calendar still works for ntfy_push /
                # vault_learn, just without skill_runner.
                def _vault_client_for_calendar():
                    # Local-name to avoid shadowing the module-level
                    # VaultClient symbol used earlier in lifespan.
                    from shared.vault_client import VaultClient as _VC
                    return _VC(
                        vault_path=config.vault_path,
                        daemon_host=config.vault_writer.host,
                        daemon_port=config.vault_writer.port,
                    )
                calendar_executor = ActionExecutor(
                    vault_client_factory=_vault_client_for_calendar,
                    image_shim=image_shim,
                    ntfy=ntfy,
                    skill_registry=skill_registry,
                    image_build_store=image_build_store,
                    vault_path=config.vault_path,
                    state_dir=state_dir,
                )
            # Holder so `_make_calendar_fire` can reach `app.state.ai_team`
            # at fire-time without a circular dependency (the factory runs
            # before AppState is fully assembled).
            calendar_app_state_holder: dict = {"state": app.state.ai_team}
            calendar_scheduler = Scheduler(
                calendar_store,
                fire=_make_calendar_fire(
                    hive_coordinator=hive_coordinator,
                    executor=calendar_executor,
                    app_state_holder=calendar_app_state_holder,
                ),
                ntfy=ntfy,
            )
            scheduler_task = calendar_scheduler.start()

            # --- Hive offline-sweep background task ----------------------------
            from gateway.deps import track_background_task
            from gateway.worker_pool.registry import sweep_offline_nodes

            async def _offline_sweep_loop() -> None:
                interval = max(1, int(config.jobs.offline_sweep_interval_s))
                while True:
                    try:
                        sweep_offline_nodes(
                            registry=node_registry,
                            dispatcher=dispatcher,
                            offline_after_s=config.nodes.heartbeat_offline_seconds,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        log.exception("hive offline sweep failed")
                    try:
                        await asyncio.sleep(interval)
                    except asyncio.CancelledError:
                        raise

            track_background_task(
                app.state.ai_team,
                asyncio.create_task(
                    _offline_sweep_loop(),
                    name="hive-offline-sweep",
                ),
            )

            # Start Crew Board dispatcher loop now that we're in a
            # running event loop. Fail-safe: keep the rest of the
            # gateway up even if the dispatcher fails to start.
            crew_dispatcher_task: asyncio.Task | None = None
            if getattr(app.state, "_crew_dispatcher_pending", False):
                try:
                    cd = app.state.crew_dispatcher
                    crew_dispatcher_task = asyncio.create_task(
                        cd.start(), name="crew-dispatcher",
                    )
                    log.info("crew dispatcher started")
                except Exception:  # noqa: BLE001
                    log.exception("crew dispatcher start failed")

            # Start Crew Board Manager daemon loop (same pattern as dispatcher).
            manager_daemon_task: asyncio.Task | None = None
            if getattr(app.state, "_manager_daemon_pending", False):
                try:
                    md = app.state.manager_daemon
                    manager_daemon_task = asyncio.create_task(
                        md.start(), name="crew-board-manager",
                    )
                    log.info("crew board manager daemon started")
                except Exception:  # noqa: BLE001
                    log.exception("crew board manager daemon start failed")

            yield
        finally:
            log.info("gateway shutting down")
            if crew_dispatcher_task is not None:
                try:
                    app.state.crew_dispatcher.stop()
                    await asyncio.wait_for(crew_dispatcher_task, timeout=5.0)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                    crew_dispatcher_task.cancel()

            # Cleanup: Crew Board Manager daemon
            if manager_daemon_task is not None:
                try:
                    app.state.manager_daemon.stop()
                    await asyncio.wait_for(manager_daemon_task, timeout=5.0)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                    log.exception("manager daemon shutdown failed")

            if auditor_scheduler is not None:
                try:
                    await auditor_scheduler.stop()
                except Exception:  # noqa: BLE001
                    log.exception("auditor scheduler stop failed")
            if groomer_idle_loop is not None:
                try:
                    await groomer_idle_loop.stop()
                except Exception:  # noqa: BLE001
                    log.exception("groomer idle loop stop failed")
            for t in (alerts_task, canon_task, recent_task, polish_task,
                      scheduler_task):
                if t is None:
                    continue
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            # Drain tracked fire-and-forget tasks (image watchers, lora
            # sub-imports, summarizer refreshes). Without this, an
            # in-flight Civitai download can be killed mid-write and
            # the AssetImportStore keeps `state="downloading"` forever.
            from gateway.deps import drain_background_tasks
            await drain_background_tasks(app.state.ai_team)
            # Drain late helpers spawned by synth-gate detach (#476 B.7).
            # Lets stragglers finish + emit helper.late telemetry before
            # the loop closes; bounded so a wedged helper can't block exit.
            if hive_coordinator is not None:
                try:
                    await asyncio.wait_for(
                        hive_coordinator._drain_late_tasks(timeout=15.0),
                        timeout=20.0,
                    )
                except asyncio.TimeoutError:
                    log.warning("late helper drain exceeded 20s; abandoning")
                except Exception:  # noqa: BLE001
                    log.exception("late helper drain failed")
            await claude_manager.close_all()

    app = FastAPI(
        title="ai-team-gateway",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS: the Lively wallpaper dashboard loads from a local HTML origin and
    # fetches the (already-open) board reads cross-origin. Mutations stay
    # token-gated regardless of CORS, and the gateway binds loopback/tailnet
    # only — so permissive CORS here is safe for this single-user surface and
    # removes the dependency on the wallpaper engine using a CORS-free browser.
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Default state for tests that don't exercise the lifespan.
    app.state.ai_team = AppState(
        config=config, devices=devices, pairing=pairing, adapters={},
        scout_history=scout_history, image_shim=image_shim,
        video_shim=video_shim,
        event_bus=event_bus, ntfy=ntfy,
        voice_pipeline=voice_pipeline,
        claude_code_manager=claude_manager,
        rate_limiter=rate_limiter,
        image_catalog=image_catalog_mod.ImageCatalog(),
        recent_images=recent_images,
        model_catalog=model_catalog,
        helpers=helpers,
        hive_coordinator=hive_coordinator,
        router=router,
        skill_registry=skill_registry,
        turn_telemetry=turn_telemetry,
        image_build_store=image_build_store,
        memory_store_hive=memory_store_hive,
        turn_log_store=turn_log_store,
        calendar_store=calendar_store,
        recipe_store=recipe_store,
        escalation_store=escalation_store,
        node_registry=node_registry,
        node_invites=node_invites,
        dispatcher=dispatcher,
        scheduler=scheduler,
    )

    app.include_router(pair_route.router)
    app.include_router(bots_route.router)
    app.include_router(chat_route.router)
    app.include_router(proactive_route.router)  # connected-brain Item 2: line 979
    app.include_router(scout_route.router)
    app.include_router(docker_route.router)
    app.include_router(gpu_mode_route.router)
    app.include_router(gitactivity_route.router)
    app.include_router(vault_route.router)
    app.include_router(graph_route.router)
    app.include_router(images_route.router)
    app.include_router(events_route.router)
    app.include_router(voice_route.router)
    app.include_router(stt_route.router)
    app.include_router(app_update_route.router)
    app.include_router(models_route.router)
    app.include_router(hive_route.router)
    app.include_router(skills_route.router)
    app.include_router(telemetry_route.router)
    app.include_router(calendar_route.router)
    app.include_router(loras_route.router)
    app.include_router(recipes_route.router)
    app.include_router(escalations_route.router)
    app.include_router(videos_route.router)
    app.include_router(config_route.router)
    app.include_router(search_route.router)
    app.include_router(digest_route.router)
    app.include_router(system_route.router)
    app.include_router(invites_route.router)
    app.include_router(node_pair_route.router)
    app.include_router(nodes_route.router)
    app.include_router(jobs_route.router)
    app.include_router(admin_route.router)
    app.include_router(suno_route.router)
    app.include_router(music_route.router)
    app.include_router(theme_route.router)
    app.include_router(appstore_route.router)
    app.include_router(terminal_route.router)
    app.include_router(wiki_route.router)
    # Crew Board: kanban for cross-project tasks. Wires after every
    # other router so its lifespan init can rely on the rest of the
    # stack being set up.
    from gateway.routes import board as board_route
    from gateway.crew_board.store import CrewBoardStore
    from gateway.crew_board.notifications import CrewNotifier
    from gateway.crew_board.project_scanner import scan as crew_scan
    from gateway.crew_board.dispatcher import CrewDispatcher
    try:
        crew_vault_path = config.vault_path
        crew_store = CrewBoardStore(
            crew_vault_path / ".vault-writer" / "vault.db"
        )
        crew_notifier = CrewNotifier(
            ntfy_topic=getattr(config, "ntfy_topic", None),
            event_bus=event_bus,
        )
        app.state.crew_store = crew_store
        app.state.crew_notifier = crew_notifier
        app.state.crew_vault_path = crew_vault_path
        # Corsair iCUE RGB → saved UI theme on boot (#189). Holds the SDK session
        # open so the colour persists; in a daemon thread so the ~5s SDK
        # handshake never delays startup. Best-effort.
        try:
            import threading as _thr
            from gateway.helpers import icue as _icue
            _saved_theme = crew_store.get_meta("ui_theme", "hive-v2") or "hive-v2"
            _thr.Thread(target=_icue.set_theme, args=(_saved_theme,),
                        name="icue-startup", daemon=True).start()
        except Exception:  # noqa: BLE001
            log.debug("iCUE startup apply skipped", exc_info=True)
        # Sync Claude Code skills into the shared vault skill store so the
        # bots (/v1/skills) and the hive loop see one canonical skill set.
        try:
            from scripts.sync_skills import sync as _sync_skills
            w, s = _sync_skills(vault_dir=crew_vault_path / "skills")
            log.info("skills sync: %d written, %d unchanged", w, s)
        except Exception:  # noqa: BLE001
            log.exception("skills sync failed (non-fatal)")
        try:
            crew_scan(crew_store)
        except Exception:  # noqa: BLE001
            log.exception("crew project scan failed at startup")
        # FLAG: app.py touched — one-line wiring for hive-lite config keys
        # (crew_hive_lite_enabled, crew_hive_lite_model, crew_parallel_lane_cap).
        # Default values keep existing behaviour 100% unchanged when not set.
        crew_dispatcher = CrewDispatcher(
            crew_store, hive_coordinator,
            vault_path=crew_vault_path,
            notifier=crew_notifier,
            daily_usd_cap=getattr(config, "crew_escalation_daily_usd_cap", 20.0),
            hive_lite_enabled=getattr(config, "crew_hive_lite_enabled", False),
            hive_lite_model=getattr(config, "crew_hive_lite_model", None),
            parallel_lane_cap=getattr(config, "crew_parallel_lane_cap", 1),
            done_retention_days=getattr(config, "crew_done_retention_days", 3.0),
            image_shim=image_shim,
            video_shim=video_shim,
            avatar_shim=avatar_shim,
        )
        app.state.crew_dispatcher = crew_dispatcher
        # The dispatcher background loop is started from the lifespan
        # hook (see _start_crew_dispatcher); we just register it here.
        app.state._crew_dispatcher_pending = True

        # Crew Board Manager daemon — autonomous board management.
        # Default: disabled until user toggles via POST /v1/crew/manager/toggle.
        try:
            from gateway.crew_board.manager_daemon import CrewBoardManager
            manager = CrewBoardManager(
                store=crew_store,
                event_bus=event_bus,
                model_catalog=model_catalog,
            )
            app.state.manager_daemon = manager
            app.state._manager_daemon_pending = True
            log.info("crew board manager: initialized")
        except Exception:  # noqa: BLE001
            log.exception("crew board manager init failed; daemon disabled")

        app.include_router(board_route.router)
        # Mount crew board manager routes (status/toggle/prompt/activity).
        from gateway.routes import manager as manager_route
        app.include_router(manager_route.router)
        log.info("crew board: wired up at /board; manager routes at /v1/crew/manager")
    except Exception:  # noqa: BLE001
        log.exception("crew board init failed; /board disabled")
    return app
