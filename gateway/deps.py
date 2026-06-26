"""Cross-cutting FastAPI dependencies + app-state helpers.

`state(request)` returns the app-wide `AppState` mounted at startup time.
`require_device` is a dependency that validates the Bearer token on REST
endpoints. `require_device_or_loopback` relaxes that for read-only routes
that the local dashboard needs without a token (loopback-only exemption).
`authenticate_ws` does the same for WebSocket handshakes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Mapping


def _make_event() -> asyncio.Event:
    # factory so dataclass field default works without a running loop
    return asyncio.Event()

log = logging.getLogger("gateway.deps")

from fastapi import Depends, HTTPException, Request, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from gateway.auth import Device, DeviceStore, PairingBroker
from gateway.config import Config


@dataclass(slots=True)
class AppState:
    config: Config
    devices: DeviceStore
    pairing: PairingBroker
    adapters: Mapping[str, Any] = field(default_factory=dict)
    scout_history: Any = None          # gateway.scout_history.ScoutHistory
    image_shim: Any = None             # gateway.image_shim.ImageShim
    video_shim: Any = None             # gateway.video_shim.VideoShim
    event_bus: Any = None               # gateway.events.EventBus
    ntfy: Any = None                   # gateway.ntfy.NtfyClient
    voice_pipeline: Any = None         # gateway.voice_shim.VoicePipeline
    claude_code_manager: Any = None    # gateway.claude_code.ClaudeCodeManager
    rate_limiter: Any = None           # gateway.rate_limit.RateLimiter
    image_catalog: Any = None          # gateway.image_catalog.ImageCatalog
    recent_images: Any = None          # gateway.recent_images.RecentImagesStore
    model_catalog: Any = None          # gateway.model_catalog.ModelCatalog
    helpers: Mapping[str, Any] = field(default_factory=dict)
    hive_coordinator: Any = None       # gateway.hive_coordinator.HiveCoordinator
    skill_registry: Any = None         # gateway.skill_registry.SkillRegistry
    turn_telemetry: Any = None         # gateway.turn_telemetry.TurnTelemetry
    image_build_store: Any = None      # gateway.image_build_state.ImageBuildStore
    memory_store_hive: Any = None     # gateway.conversation_memory.MemoryStore
    turn_log_store: Any = None         # gateway.turn_log.TurnLogStore
    calendar_store: Any = None         # gateway.calendar_jobs.JobStore
    asset_import_store: Any = None     # gateway.asset_importer.AssetImportStore
    recipe_store: Any = None           # gateway.recipe_store.RecipeStore
    escalation_store: Any = None       # gateway.escalation_store.EscalationStore
    router: Any = None                  # gateway.orchestrator.router.Router
    auditor_scheduler: Any = None      # gateway.auditor.scheduler.AuditorScheduler
    groomer_idle_loop: Any = None      # vault_writer.groomer.idle_loop.IdleGroomerLoop
    last_turn_completed_at: float = 0.0
    node_registry: Any = None          # gateway.worker_pool.registry.NodeRegistry
    node_invites: Any = None           # gateway.worker_pool.invites.InviteBroker
    dispatcher: Any = None              # gateway.worker_pool.dispatcher.Dispatcher
    scheduler: Any = None               # gateway.worker_pool.scheduler.Scheduler
    vault_client: Any = None           # shared.vault_client.VaultClient — read by chat thread/pin/fork/search-chat routes
    ollama_probe_result: Any = None    # gateway.ollama_probe.ProbeResult — set by lifespan after prewarm (#438)
    # Pending [CONFIRM_IMAGE] payloads keyed by device_id. The next user
    # message either confirms ("yes/go"), cancels ("no/cancel"), or implicitly
    # drops the pending (anything else — clears so Hive can re-propose).
    pending_image_confirms: dict[str, dict] = field(default_factory=dict)
    # Pending img2img reference media keyed by device_id. Cleared the moment
    # an image generation actually fires.
    pending_image_refs: dict[str, str] = field(default_factory=dict)
    # Tracked fire-and-forget tasks. Anything that uses
    # `asyncio.create_task(...)` inside the gateway should register here
    # via `track_background_task`, so lifespan shutdown can cancel +
    # await them. Without this, in-flight image watchers, summarizer
    # refreshes, and 12 GB Civitai downloads die mid-write on shutdown
    # and their stores keep `state="downloading"` forever.
    background_tasks: set = field(default_factory=set)
    # Set while a hive WS turn is in flight; cleared when it completes or errors.
    # Background tasks (groomer, auditor) must wait on this before making LLM calls.
    hive_turn_active: asyncio.Event = field(default_factory=asyncio.Event)


_LONG_TASK_PREFIX = "long:"
"""Prefix on `asyncio.Task` name that marks a task as long-running so
shutdown gives it a more generous drain budget. Use it for things like
multi-GB Civitai downloads that legitimately take minutes; default
short tasks (image watchers, summarizer refreshes) get the fast 8s
budget."""


def track_background_task(
    app_state: "AppState", task: "asyncio.Task[Any]",
) -> "asyncio.Task[Any]":
    """Add `task` to the AppState's tracked-task set so it can be drained
    on shutdown. Auto-discards on completion so the set doesn't grow
    unbounded over a long-running session. Safe to call with a task that
    isn't from the gateway's own event loop.

    Long-running tasks (e.g. multi-GB downloads) should set their task
    name with the `_LONG_TASK_PREFIX` so shutdown drain gives them a
    longer timeout — see `drain_background_tasks` for the tiering."""
    app_state.background_tasks.add(task)
    task.add_done_callback(app_state.background_tasks.discard)
    return task


async def drain_background_tasks(
    app_state: "AppState",
    *,
    short_timeout_s: float = 8.0,
    long_timeout_s: float = 60.0,
) -> None:
    """Cancel + await every tracked task. Called from app lifespan
    shutdown. Tasks whose name starts with `_LONG_TASK_PREFIX` get the
    `long_timeout_s` budget (default 60s) so a partway-done multi-GB
    download has a chance to flush its in-progress chunk to disk before
    the process exits. Everything else uses `short_timeout_s` (8s) so
    routine background work doesn't block Ctrl-C."""
    tasks = list(app_state.background_tasks)
    if not tasks:
        return
    short, long_ = [], []
    for t in tasks:
        name = (t.get_name() or "")
        (long_ if name.startswith(_LONG_TASK_PREFIX) else short).append(t)
    log.info(
        "shutdown: draining %d short + %d long background tasks",
        len(short), len(long_),
    )
    for t in tasks:
        if not t.done():
            t.cancel()
    # Drain short first so we don't block the process on the slow group.
    for group, budget, label in (
        (short, short_timeout_s, "short"),
        (long_, long_timeout_s, "long"),
    ):
        if not group:
            continue
        try:
            await asyncio.wait_for(
                asyncio.gather(*group, return_exceptions=True),
                timeout=budget,
            )
        except asyncio.TimeoutError:
            log.warning(
                "shutdown: %d %s tasks still running after %.1fs",
                sum(1 for t in group if not t.done()), label, budget,
            )


def state(conn) -> AppState:
    """Extract AppState from either a Request or a WebSocket."""
    return conn.app.state.ai_team


_bearer = HTTPBearer(auto_error=False)


def require_device(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Device:
    """REST dependency: 401 unless the Bearer token resolves to a live device.

    Defense-in-depth (F-1): explicitly rejects a token that belongs to a hive
    node, even though the two registries never share a token space in practice.
    """
    st = state(request)
    token = credentials.credentials if credentials else None
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    device = st.devices.verify(token)
    if device is None:
        raise HTTPException(status_code=401, detail="invalid token")
    # Cross-reject: if this token also authenticates against the node registry,
    # it is a node token and must not be accepted for a device-only endpoint.
    if st.node_registry is not None and st.node_registry.verify_token(token) is not None:
        raise HTTPException(
            status_code=401,
            detail="token belongs to a hive node, not a device",
        )
    st.devices.touch(device.id)
    return device


# ── Loopback helpers ──────────────────────────────────────────────────────────

_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


def _is_loopback(host: str) -> bool:
    """True iff the remote address is on the loopback interface.

    Accepts the full 127.0.0.0/8 range (some WebView2 builds report
    addresses other than 127.0.0.1).  Tailscale (100.x) and LAN IPs
    are rejected.
    """
    if host in _LOOPBACK_HOSTS:
        return True
    if host.startswith("127."):
        try:
            parts = host.split(".")
            return len(parts) == 4 and all(p.isdigit() for p in parts)
        except Exception:  # noqa: BLE001
            return False
    return False


def require_device_or_loopback(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Device | None:
    """REST dependency for read-only routes accessible from the local dashboard.

    Passes the request through if EITHER:
      • the request carries a valid device Bearer token (same check as
        ``require_device``), OR
      • the request originates from a loopback address (127.0.0.0/8, ::1,
        ::ffff:127.0.0.1) — which covers the Lively WebView2 wallpaper running
        on the same machine.

    Tailnet / remote callers that lack a token are rejected with 401 — the
    loopback exemption is strictly local-only.

    Returns the authenticated ``Device`` when a token is present, or ``None``
    when the loopback path is taken (callers that need the device object should
    use ``require_device`` instead).
    """
    # ── Token path: check first (fast, also works from tailnet) ──────────────
    token = credentials.credentials if credentials else None
    if token:
        st = state(request)
        device = st.devices.verify(token)
        if device is None:
            raise HTTPException(status_code=401, detail="invalid token")
        if st.node_registry is not None and st.node_registry.verify_token(token) is not None:
            raise HTTPException(
                status_code=401,
                detail="token belongs to a hive node, not a device",
            )
        st.devices.touch(device.id)
        return device

    # ── Loopback path: no token required when the caller is local ─────────────
    client = request.client
    remote_host = client.host if client else ""
    if _is_loopback(remote_host):
        return None

    # ── Remote caller without token → 401 ─────────────────────────────────────
    raise HTTPException(status_code=401, detail="missing bearer token")


def require_node(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
):
    """REST dependency: 401 unless Bearer resolves to a non-revoked hive node.

    Distinct from `require_device`: nodes auth against `node_registry`,
    never against the user's device store. The two registries do not
    share token spaces.

    Defense-in-depth (F-1): explicitly rejects a token that belongs to a
    device, even though the two registries never share a token space in practice.
    """
    st = state(request)
    token = credentials.credentials if credentials else None
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    node = st.node_registry.verify_token(token) if st.node_registry else None
    if node is None:
        raise HTTPException(status_code=401, detail="invalid node token")
    # Cross-reject: if this token also authenticates against the device store,
    # it is a device token and must not be accepted for a node-only endpoint.
    if st.devices.verify(token) is not None:
        raise HTTPException(
            status_code=401,
            detail="token belongs to a device, not a hive node",
        )
    return node


def rate_limited(bucket: str):
    """Dependency factory enforcing a named token bucket per authenticated device."""
    def _check(
        request: Request,
        device: Device = Depends(require_device),
    ) -> Device:
        st = state(request)
        limiter = st.rate_limiter
        if limiter is not None and not limiter.try_acquire(device.id, bucket):
            raise HTTPException(
                status_code=429, detail=f"rate limit '{bucket}' exceeded",
            )
        return device
    return _check


async def authenticate_ws(websocket: WebSocket, app_state: AppState) -> Device | None:
    """Check Authorization header or ?token=... query param. Close on failure."""
    token: str | None = None
    auth = websocket.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth.split(None, 1)[1].strip()
    if not token:
        token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="missing token")
        return None
    device = app_state.devices.verify(token)
    if device is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="invalid token")
        return None
    app_state.devices.touch(device.id)
    return device
