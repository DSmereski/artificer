"""WebSocket chat route. One connection per open thread.

Client protocol (JSON per line):
  C -> S: {"type": "user", "text": "...", "user_id": 123}
  S -> C: {"type": "assistant", "seq": 0, "text": "chunk"}
  S -> C: {"type": "image_pending", "job_id": "...", "prompt": "..."}
  S -> C: {"type": "image_done", "job_id": "...", "media_id": "..."}
  S -> C: {"type": "image_confirm", "payload": {...resolved spec...}}
  S -> C: {"type": "memory_saved", "path": "...", "title": "..."}
  S -> C: {"type": "done"}
  S -> C: {"type": "error", "message": "..."}

Hive's marker-aware flow (handled in this route, transparent to other bots):

  [GENERATE_IMAGE] {...}   → render now (one-shot or already-confirmed)
  [CONFIRM_IMAGE]  {...}   → propose payload to user, halt this turn
  [ASK_USER]       <q>     → ask question, halt this turn
  [REMEMBER]       {...}   → write a vault note via VaultClient.learn
  [VAULT_LOOKUP]   <q>     → re-feed vault hits and let Hive continue

All markers are stripped from the visible reply before streaming back.
Other bots (Maggy / Scout / Claude Code) keep the streaming path unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status

from gateway.deps import (
    authenticate_ws, require_device, state, track_background_task,
)


router = APIRouter(prefix="/v1", tags=["chat"])
log = logging.getLogger("gateway.chat")


# How long we'll wait for an image job before giving up on WS delivery.
# SDXL + enhance on a loaded GPU can legitimately take 5+ minutes. We only
# *declare* a job failed if the job itself errors — a WS-side timeout only
# stops the pending bubble from looking like it's hung forever.
_IMAGE_TIMEOUT_SECONDS = 600.0


def _stable_user_id(seed: str) -> int:
    """Derive a deterministic 32-bit user_id from any seed string.

    Python's built-in `hash()` is salted per-process and intentionally
    collision-prone for DoS resistance. That broke history routing across
    gateway restarts. md5 is consistent across runs and gives us a clean
    32-bit slot for the LLM history file naming.

    Callers pass `device.user` (the logical owner string, default
    "owner") so phone + PC sharing the same user see the same chat
    history. Earlier code passed `device.id` which made every device
    its own conversation island — phone history invisible to PC.
    """
    digest = hashlib.md5(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0xFFFFFFFF


# ---------------------------------------------------------------- payload resolution


def _resolve_image_kwargs(payload: dict, app_state) -> dict:
    """Resolve a [GENERATE_IMAGE]/[CONFIRM_IMAGE] payload into shim.enqueue kwargs.

    Applies preset → aspect → explicit overrides → LoRA strategy in that order
    so the user's explicit JSON always wins over preset defaults.
    """
    from gateway import image_catalog as cat_mod

    config = app_state.config
    catalog = app_state.image_catalog or cat_mod.ImageCatalog()

    kwargs: dict = {
        "prompt": payload["prompt"],
        "count": int(payload.get("count", 1)),
        "enhance": bool(payload.get("enhance", True)),
    }
    if payload.get("model"):
        kwargs["model"] = payload["model"]

    preset = cat_mod.resolve_preset(catalog, payload.get("preset"))
    if preset is not None:
        kwargs["width"] = preset.width
        kwargs["height"] = preset.height
        kwargs["steps"] = preset.steps
        kwargs["guidance"] = preset.guidance
        if preset.negative and "negative" not in payload:
            kwargs["negative_prompt"] = preset.negative
        preset_loras = list(preset.loras or [])
    else:
        preset_loras = []

    size = cat_mod.resolve_aspect(payload.get("aspect"))
    if size is not None:
        kwargs["width"], kwargs["height"] = size
    if "negative" in payload:
        kwargs["negative_prompt"] = payload["negative"]
    if "steps" in payload:
        kwargs["steps"] = int(payload["steps"])
    if "guidance" in payload:
        kwargs["guidance"] = float(payload["guidance"])

    lora_overrides: list[dict] | None = None
    if "loras" in payload:
        raw = [a for a in (payload["loras"] or []) if a]
        if raw:
            lora_overrides = cat_mod.resolve_lora_aliases(raw, catalog)
        else:
            lora_overrides = []  # explicit opt-out
    elif preset_loras:
        lora_overrides = preset_loras
    elif config.images.auto_lora:
        picked = cat_mod.pick_auto_loras(
            payload["prompt"],
            image_app_root=config.images.image_app_root,
            model_choice=kwargs.get("model"),
            max_loras=config.images.max_auto_loras,
            catalog=catalog,
        )
        if picked:
            log.info(
                "auto-lora picked %d for prompt=%r: %s",
                len(picked), payload["prompt"][:60],
                [p.get("choice", "") for p in picked],
            )
            lora_overrides = picked

    if lora_overrides is not None:
        kwargs["lora_overrides"] = lora_overrides
    return kwargs


def _kwargs_to_summary(kwargs: dict) -> dict:
    """A safe-to-show summary of resolved kwargs for [CONFIRM_IMAGE] events."""
    out = {
        "prompt": kwargs.get("prompt", ""),
        "count": kwargs.get("count", 1),
        "width": kwargs.get("width", 1024),
        "height": kwargs.get("height", 1024),
        "steps": kwargs.get("steps", 20),
        "guidance": kwargs.get("guidance", 3.5),
        "negative_prompt": kwargs.get("negative_prompt", ""),
        "enhance": kwargs.get("enhance", True),
    }
    loras = kwargs.get("lora_overrides")
    if loras is not None:
        out["loras"] = [l.get("choice", "") for l in loras if isinstance(l, dict)]
    if kwargs.get("model"):
        out["model"] = kwargs["model"]
    return out


# ---------------------------------------------------------------- image rendering


async def _render_and_stream(
    websocket: WebSocket,
    app_state,
    kwargs: dict,
    *,
    device_id: str,
) -> None:
    """Enqueue an already-resolved image job, ledger it, and stream events."""
    shim = app_state.image_shim
    bus = app_state.event_bus
    if shim is None or bus is None:
        await websocket.send_json(
            {"type": "error", "message": "image pipeline not configured"}
        )
        return

    # Per-device img2img reference: if the user attached an image since the
    # last render, route this turn through img2img and clear the slot.
    pending_refs = app_state.pending_image_refs
    if pending_refs is not None and device_id in pending_refs:
        from gateway.routes.images import _resolve_uploaded_reference
        media_id = pending_refs.pop(device_id)
        ref_path = _resolve_uploaded_reference(
            app_state.config.state_dir, media_id,
        )
        if ref_path is not None:
            kwargs["reference_path"] = str(ref_path)
            # Apply provided strength if Hive/payload set one; else default.
            kwargs.setdefault("strength", 0.6)
            log.info(
                "img2img: using ref %s for prompt=%r",
                media_id, kwargs.get("prompt", "")[:60],
            )

    job = await shim.enqueue(**kwargs)
    recent = app_state.recent_images
    if recent is not None:
        recent.record(
            device_id=device_id,
            bot="hive",
            job_id=job.id,
            prompt=kwargs["prompt"],
        )
    await websocket.send_json(
        {"type": "image_pending", "job_id": job.id, "prompt": kwargs["prompt"]}
    )

    queue = await bus.subscribe(f"chat-image-{job.id}")
    try:
        deadline = asyncio.get_running_loop().time() + _IMAGE_TIMEOUT_SECONDS
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                fresh = shim.get(job.id)
                if fresh is not None and fresh.state == "done" and fresh.result_ids:
                    await websocket.send_json({
                        "type": "image_done", "job_id": job.id,
                        "media_id": fresh.result_ids[0],
                    })
                    return
                if fresh is not None and fresh.state == "error":
                    await websocket.send_json({
                        "type": "error",
                        "message": fresh.error or "image failed",
                    })
                    return
                await websocket.send_json({
                    "type": "image_slow", "job_id": job.id,
                    "message": "still rendering; will appear when ready",
                })
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                continue
            if event.get("type") != "image_done" or event.get("job_id") != job.id:
                continue
            if event.get("state") == "done" and event.get("result_ids"):
                await websocket.send_json({
                    "type": "image_done", "job_id": job.id,
                    "media_id": event["result_ids"][0],
                })
            else:
                await websocket.send_json({
                    "type": "error",
                    "message": event.get("error") or "image failed",
                })
            return
    finally:
        await bus.unsubscribe(queue)


# ---------------------------------------------------------------- hive turn


async def _run_hive_turn_cancel_on_disconnect(
    websocket: WebSocket,
    app_state,
    *,
    coord,
    user_id: int,
    text: str,
    device_id: str,
    device_audience: list[str] | None,
    thread_id: str = "default",
) -> str | None:
    """Run `_hive_turn` but cancel it if the WS drops mid-turn.

    Returns the turn's `turn_id` when it completed normally, so the
    dispatcher can stamp it on the trailing `done` frame. WS-disconnect
    paths raise WebSocketDisconnect (no return value) — that exception
    bypasses the trailing `done` send entirely.

    Side-effects (vault writes, image renders, ntfy pushes) are NOT
    things we want firing after the user has already left. Starlette
    doesn't auto-cancel the awaited task on disconnect, so we race
    the turn against `websocket.receive_text()` — if that returns
    (or raises WebSocketDisconnect) before the turn completes, we
    cancel the turn task and re-raise WebSocketDisconnect to trigger
    the standard cleanup path.
    """
    turn_task = asyncio.create_task(
        _hive_turn(
            websocket, app_state, coord=coord,
            user_id=user_id, text=text,
            device_id=device_id, device_audience=device_audience,
            thread_id=thread_id,
        ),
        name=f"hive_turn:{device_id}",
    )
    # Use a small probe-loop instead of a one-shot receive: the WS
    # is kept open for the duration of the turn (we don't expect new
    # text from the user mid-turn), so a single `receive_text()`
    # would pile up another message we'd then have to replay. We
    # only need to know when the connection drops.
    async def _watch_disconnect() -> None:
        try:
            while True:
                # `receive` returns a dict whose `type` is
                # 'websocket.receive' for messages or
                # 'websocket.disconnect' on close.
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    return
                # A new message came in while a turn was running.
                # Drop it on the floor — the user shouldn't be
                # double-sending and accepting it would race.
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001
            return

    watch_task = asyncio.create_task(
        _watch_disconnect(), name=f"ws_watch:{device_id}",
    )
    try:
        done, pending = await asyncio.wait(
            {turn_task, watch_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if watch_task in done and turn_task not in done:
            # Disconnect won the race — cancel the turn so its
            # actions don't execute against a vanished user. We
            # explicitly drop helper IO/state (vault writes, image
            # renders) by interrupting; `_hive_turn` doesn't have a
            # clean cancellation barrier today, so the safest thing
            # is to let CancelledError propagate through the
            # coordinator's helper gather. Any in-flight
            # ActionExecutor side-effect that already started
            # finishes; everything queued behind it stops.
            log.info(
                "chat: WS disconnect during turn — cancelling",
            )
            turn_task.cancel()
            try:
                await turn_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            raise WebSocketDisconnect()
        # Turn finished; tear down the watcher.
        if not watch_task.done():
            watch_task.cancel()
            try:
                await watch_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # Surface any exception from the turn task (the original
        # _hive_turn behaviour was to let exceptions propagate).
        exc = turn_task.exception()
        if exc is not None:
            raise exc
        # Forward the coordinator-assigned turn_id so the dispatcher
        # can carry it in the trailing `done` frame. _hive_turn returns
        # str | None.
        return turn_task.result()
    finally:
        for t in (turn_task, watch_task):
            if not t.done():
                t.cancel()


def _maybe_touch_and_title_thread(
    app_state, *, turn, bot: str, user_id: int, text: str, thread_id: str,
) -> None:
    """Backfill missing chat_thread rows + bump last_active_at.

    The WS handshake accepts an arbitrary `?thread_id=` from the
    client. If the client never called POST /threads first, the
    chat_thread row won't exist — without this, the thread silently
    never appears in the /threads listing. Fires `thread_create`
    (idempotent INSERT OR IGNORE) on every turn so the row materialises
    on the first turn for a given thread_id, then `thread_touch` to
    bump last_active_at for the sidebar sort.

    Title heuristic: first 50 characters of the first user message,
    set ONLY when the thread didn't exist (so we never clobber a
    user-set title). Cheap, deterministic, no LLM round-trip needed.
    """
    from gateway.deps import track_background_task

    if turn is None or turn.error or turn.blocked or not turn.reply:
        return
    vc = app_state.vault_client
    if vc is None:
        return
    title = (text or "").strip().splitlines()[0][:50] if text else ""
    if not title:
        title = "(untitled)"
    try:
        track_background_task(
            app_state,
            asyncio.create_task(
                vc.thread_create(
                    thread_id=thread_id, bot=bot, user_id=user_id,
                    title=title,
                ),
                name=f"thread_create_idem:{thread_id}",
            ),
        )
        track_background_task(
            app_state,
            asyncio.create_task(
                vc.thread_touch(thread_id=thread_id),
                name=f"thread_touch:{thread_id}",
            ),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("thread touch/title failed: %s", e)


async def _hive_turn(
    websocket: WebSocket,
    app_state,
    *,
    coord,
    user_id: int,
    text: str,
    device_id: str,
    device_audience: list[str] | None,
    thread_id: str = "default",
) -> str | None:
    """Drive a Hive turn through the HiveCoordinator.

    Pure orchestrator — every concrete step is a one-liner into
    `gateway.hive_turn_helpers`. The body order is load-bearing
    though: the `finally` is what guarantees that a mid-bridge WS
    disconnect still gets the reply persisted to chat history.

    Returns the coordinator-assigned `turn_id` (e.g. `tk-ab12cd34`)
    when the turn ran to completion, so the dispatcher can carry it
    in the trailing `done` WS frame. Returns None if the turn was
    cancelled before `coord.coordinate` produced an AssistantTurn.
    """
    from gateway.chat_image_bridge import forward_image_receipts
    from gateway.event_emitter import WebSocketEmitter
    from gateway.hive_turn_helpers import (
        build_turn_context_async,
        index_hive_turn_to_chat_log,
        maybe_auto_title_thread,
        persist_hive_turn_history,
        publish_turn_done_notifications,
        record_turn_log,
        record_turn_telemetry,
        schedule_summarizer_refresh,
    )

    send_state = {"disconnected": False}

    async def _send(payload):
        if send_state["disconnected"]:
            return
        try:
            await websocket.send_json(payload)
        except (WebSocketDisconnect, RuntimeError) as e:
            # Expected when the client drops mid-turn: Starlette raises
            # WebSocketDisconnect, or RuntimeError ("Cannot call send
            # once a close message has been sent"). The disconnect
            # watcher cancels the turn separately; stop trying to send
            # and don't spam the log on every subsequent frame.
            send_state["disconnected"] = True
            log.debug("chat: WS send skipped — connection closed: %s", e)
        except Exception:  # noqa: BLE001
            # Anything else is a real bug we were previously hiding.
            send_state["disconnected"] = True
            log.warning("chat: unexpected WS send failure", exc_info=True)
    quiet = (
        websocket.query_params.get("verbosity", "").lower() == "quiet"
    )
    emitter = WebSocketEmitter(send_json=_send, quiet=quiet)

    ctx = await build_turn_context_async(
        app_state,
        user_id=user_id, text=text,
        device_id=device_id, device_audience=device_audience,
        thread_id=thread_id,
    )

    app_state.hive_turn_active.set()
    turn = None
    try:
        try:
            turn = await coord.coordinate(ctx, emitter)
            await forward_image_receipts(
                websocket, app_state,
                receipts=list(turn.receipts), device_id=device_id,
            )
        finally:
            app_state.hive_turn_active.clear()
            # Sits in `finally` so a mid-bridge WS disconnect still records
            # the reply — without this the phone reconnects, sees an empty
            # bubble, and the turn appears to have vanished.
            persist_hive_turn_history(
                app_state, turn, user_id=user_id, text=text,
            )
            index_hive_turn_to_chat_log(
                app_state, turn, user_id=user_id, text=text,
                thread_id=thread_id,
            )

        # Post-turn observability + fan-out. Past this point `turn` is
        # always set (we'd have returned via the finally if not).
        record_turn_telemetry(app_state, turn, device_id=device_id, text=text)
        await record_turn_log(
            app_state, turn,
            user_id=user_id, device_id=device_id, text=text,
        )
        await publish_turn_done_notifications(
            app_state, turn, device_id=device_id,
        )
        schedule_summarizer_refresh(
            app_state, turn, user_id=user_id, text=text,
            thread_id=thread_id,
        )

        # Touch the thread's last_active_at + apply the heuristic title on
        # first turn. Both are best-effort background tasks tracked via
        # deps.track_background_task.
        _maybe_touch_and_title_thread(
            app_state, turn=turn,
            bot="hive", user_id=user_id, text=text, thread_id=thread_id,
        )

        # Phase 2.6: at exactly turn 3, replace the heuristic
        # first-50-chars title with an LLM-generated 2–6-word title. Reads
        # the per-thread turn counter that schedule_summarizer_refresh
        # incremented above; MUST run after that call.
        if turn is not None and not turn.error and not turn.blocked:
            maybe_auto_title_thread(
                app_state,
                bot="hive", user_id=user_id, text=text,
                thread_id=thread_id,
            )
    finally:
        # ALWAYS close the emitter — including on cancellation from the
        # WS disconnect watcher. Without this, the flush task survives
        # past the turn coroutine and only stops when the WS itself
        # closes; in tests with isolated loops it leaks past loop
        # teardown and surfaces as RuntimeError("Event loop is closed").
        await emitter.close()
    return getattr(turn, "turn_id", None) or None


# ---------------------------------------------------------------- WebSocket entry


@router.post("/chat/{bot}/reset")
async def reset_chat(
    bot: str,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Wipe the calling device's chat history with `bot`.

    Used when the LLM gets stuck in a confused loop and the cleanest
    out is starting fresh. The history file is removed; vault canon and
    other persistent state are unaffected.
    """
    app_state = state(request)
    adapter = app_state.adapters.get(bot)
    if adapter is None:
        raise HTTPException(status_code=404, detail="unknown bot")
    llm = getattr(adapter, "_llm", None)
    if llm is None or not hasattr(llm, "reset_history"):
        raise HTTPException(status_code=400, detail="bot has no resettable history")
    user_id = _stable_user_id(device.user)
    try:
        llm.reset_history(user_id)
    except Exception as e:  # noqa: BLE001
        log.warning("reset_history failed for %s/%s: %s", bot, user_id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    # Also drop any pending per-device confirms or refs so the next
    # message starts truly clean.
    for slot in ("pending_image_confirms", "pending_image_refs"):
        d = getattr(app_state, slot, None)
        if isinstance(d, dict):
            d.pop(device.id, None)
    # Wipe the MemoryStore sidecar too — without this, stale
    # `mid_user_facts` / `mid_open_tasks` from the prior conversation
    # bleed back into the next turn's planner prompt and the user
    # thinks the reset didn't take.
    memory_store = getattr(app_state, f"memory_store_{bot}", None)
    if memory_store is not None and hasattr(memory_store, "reset"):
        try:
            # thread_id=None drops every thread for this user — the
            # legacy /reset behaviour is to wipe the whole conversation.
            #
            # on_chat_log_clear (#444): wire vault_client.chat_log_clear so
            # the daemon's SQLite chat_log rows are wiped alongside the sidecar.
            # Without this, prior-session context leaks back via recent_chat()
            # (security finding #444 / commit 71da65e). The callback is sync
            # (schedules a background task) to keep MemoryStore decoupled from
            # the event loop. Uses async def reset_chat so create_task works.
            vc = app_state.vault_client
            if vc is not None and hasattr(vc, "chat_log_clear"):
                def _on_clear(uid: int, _bot: str) -> None:
                    track_background_task(
                        app_state,
                        asyncio.create_task(
                            vc.chat_log_clear(bot=_bot, user_id=uid),
                            name=f"chat_log_clear:{_bot}:{uid}",
                        ),
                    )
                on_chat_log_clear = _on_clear
            else:
                on_chat_log_clear = None
            memory_store.reset(user_id, thread_id=None,
                               on_chat_log_clear=on_chat_log_clear)
        except Exception as e:  # noqa: BLE001
            log.warning("memory_store reset failed: %s", e)
    return {"ok": True, "bot": bot}


@router.get("/chat/{bot}/last_reply")
def last_reply(
    bot: str,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Return the most recent assistant message persisted for the
    calling device's chat with `bot`.

    Used by the phone app to recover a turn whose WebSocket dropped
    while the app was backgrounded — the gateway finishes the turn
    server-side and the reply lands in the LLM history file even if
    the live event stream never reached the client. The app calls
    this on app resume to fill any stuck "..." bubble.
    """
    app_state = state(request)
    adapter = app_state.adapters.get(bot)
    if adapter is None:
        raise HTTPException(status_code=404, detail="unknown bot")
    llm = getattr(adapter, "_llm", None)
    if llm is None:
        return {"reply": None}
    user_id = _stable_user_id(device.user)
    history = getattr(llm, "_history", None)
    if not isinstance(history, dict):
        return {"reply": None}
    turns = history.get(user_id) or []
    # Walk from the end for the most recent assistant message.
    for entry in reversed(turns):
        if not isinstance(entry, dict):
            continue
        if entry.get("role") == "assistant":
            text = str(entry.get("content") or "")
            if text.strip():
                return {"reply": text}
            return {"reply": None}
    return {"reply": None}


@router.get("/chat/{bot}/messages")
def chat_messages(
    bot: str,
    limit: int = 50,
    thread_id: str | None = None,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Return the most recent `limit` messages for the calling
    device's user. Used by the app's chat tab on open to back-fill
    the bubble list with prior conversation — without this the tab
    booted empty every session even though server-side history
    existed.

    Each item is `{role: "user"|"assistant", content: "..."}` in
    chronological order (oldest first). Hive-driven turns are now
    captured here too (see `_hive_turn` → `record_turn`).

    History source priority (merges both, deduplicates):
      1. chat_log (persistent SQLite) — survives restarts, never loses
         turns that aged out of the rolling buffer.
      2. LLMClient._history (in-memory rolling buffer) — used as a
         supplement when the persistent table is unavailable.

    Without consulting chat_log, any turns older than _MAX_HISTORY
    (200 entries) in the rolling buffer silently disappear on
    relaunch — that was the root cause of the text-messages-missing
    bug reported on 2026-04-29.
    """
    app_state = state(request)
    adapter = app_state.adapters.get(bot)
    if adapter is None:
        raise HTTPException(status_code=404, detail="unknown bot")
    user_id = _stable_user_id(device.user)
    n = max(1, min(int(limit), 200))

    # --- primary source: persistent chat_log via VaultClient ----------
    vc = app_state.vault_client
    if vc is not None and hasattr(vc, "recent_chat"):
        try:
            msgs = vc.recent_chat(
                bot=bot, user_id=user_id, limit=n,
                thread_id=thread_id,
            )
            if msgs:
                return {"messages": msgs}
        except Exception as e:  # noqa: BLE001
            log.warning("chat_messages: recent_chat failed, falling back: %s", e)

    # --- fallback: in-memory rolling buffer (LLMClient._history) ------
    # Rolling buffer is keyed by user_id only — no thread split. If the
    # caller asked for a specific thread we cannot honour that here, so
    # return empty rather than leak cross-thread history.
    if thread_id is not None:
        return {"messages": []}
    llm = getattr(adapter, "_llm", None)
    if llm is None:
        return {"messages": []}
    if hasattr(llm, "recent_messages"):
        msgs = llm.recent_messages(user_id, limit=n)
    else:
        history = getattr(llm, "_history", None) or {}
        msgs = list(history.get(user_id, []))[-n:]
    return {"messages": msgs}


@router.get("/chat/{bot}/search")
async def chat_search(
    bot: str,
    q: str,
    limit: int = 20,
    thread_id: str | None = None,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """FTS5 search over the calling user's chat history with `bot`.

    The chat_log table is populated by `index_hive_turn_to_chat_log`
    in the WS finally-block, so this returns turns even after they've
    aged out of the verbatim-history rolling buffer.

    When `feature_search_llm_rerank` is enabled in the gateway config,
    the top-20 RRF candidates are semantically re-ordered by the cheap
    LLM before being returned. The `chat_recall` helper bypasses this
    route entirely (direct `VaultClient.search_chat` call) so the
    turn-time path is never affected."""
    app_state = state(request)
    if bot not in (app_state.adapters or {}):
        raise HTTPException(status_code=404, detail="unknown bot")
    if not q or not q.strip():
        return {"results": []}
    vc = app_state.vault_client
    if vc is None or not hasattr(vc, "search_chat"):
        return {"results": []}
    user_id = _stable_user_id(device.user)
    n = max(1, min(int(limit), 50))
    try:
        rows = vc.search_chat(
            bot=bot, user_id=user_id, query_text=q, limit=n,
            thread_id=thread_id,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("chat search failed: %s", e)
        return {"results": []}

    # LLM re-rank: user-initiated path only. Controlled by the feature
    # flag. The chat_recall helper calls VaultClient.search_chat directly
    # (not this route) so the turn-time path is never affected.
    cfg = app_state.config
    if getattr(cfg, "feature_search_llm_rerank", False) and rows:
        try:
            from gateway.search_rerank import llm_rerank
            rows = await llm_rerank(q, rows, limit=n)
        except Exception as e:  # noqa: BLE001
            log.warning("chat_search: rerank failed (returning RRF order): %s", e)

    return {"results": rows}


# ---------------------------------------------------------------- threads


def _new_thread_id() -> str:
    """Same shape as device IDs — URL-safe random token. Not a ULID
    but ULID's only meaningful feature here (sortability) is already
    covered by chat_thread.last_active_at."""
    import secrets
    return secrets.token_urlsafe(12)


_MAX_THREAD_ID_LEN = 64


def _validate_thread_id(raw: str) -> str:
    """Reject malformed thread IDs from the WS query string.

    Allow URL-safe chars only (letters, digits, `-`, `_`) and cap the
    length so a broken client can't spam memory sidecars across a
    billion paths. On any rejection fall back to "default" — a slightly
    surprising thread is better than a 4xx that bricks chat.
    """
    if not raw:
        return "default"
    if len(raw) > _MAX_THREAD_ID_LEN:
        return "default"
    for ch in raw:
        if not (ch.isalnum() or ch in "-_"):
            return "default"
    return raw


def _vc_or_404(app_state):
    vc = app_state.vault_client
    if vc is None:
        raise HTTPException(
            status_code=503, detail="vault client not configured",
        )
    return vc


@router.get("/chat/{bot}/threads")
def list_threads(
    bot: str,
    include_archived: bool = False,
    limit: int = 100,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """List the calling user's threads with `bot`, newest-first."""
    app_state = state(request)
    if bot not in (app_state.adapters or {}):
        raise HTTPException(status_code=404, detail="unknown bot")
    vc = _vc_or_404(app_state)
    user_id = _stable_user_id(device.user)
    rows = vc.list_threads(
        bot=bot, user_id=user_id,
        include_archived=bool(include_archived),
        limit=max(1, min(int(limit), 500)),
    )
    return {"threads": rows}


@router.post("/chat/{bot}/threads")
async def create_thread(
    bot: str,
    payload: dict | None = None,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Create a new conversation thread with `bot`. Optional
    `title` in the body; if absent, the first user turn is used to
    auto-title later (P2.6)."""
    app_state = state(request)
    if bot not in (app_state.adapters or {}):
        raise HTTPException(status_code=404, detail="unknown bot")
    vc = _vc_or_404(app_state)
    user_id = _stable_user_id(device.user)
    title = None
    if isinstance(payload, dict):
        raw_title = payload.get("title")
        if isinstance(raw_title, str) and raw_title.strip():
            title = raw_title.strip()[:200]
    thread_id = _new_thread_id()
    resp = await vc.thread_create(
        thread_id=thread_id, bot=bot, user_id=user_id, title=title,
    )
    if not resp or not resp.get("ok"):
        raise HTTPException(
            status_code=502,
            detail=(resp or {}).get("error", "thread create failed"),
        )
    return {"id": thread_id, "title": title}


@router.post("/chat/{bot}/threads/{thread_id}/archive")
async def archive_thread(
    bot: str, thread_id: str,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    app_state = state(request)
    if bot not in (app_state.adapters or {}):
        raise HTTPException(status_code=404, detail="unknown bot")
    vc = _vc_or_404(app_state)
    user_id = _stable_user_id(device.user)
    # Ownership check — never let a device archive someone else's thread.
    meta = vc.get_thread(thread_id)
    if meta is None or meta["bot"] != bot or meta["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="unknown thread")
    resp = await vc.thread_archive(thread_id=thread_id)
    if not resp or not resp.get("ok"):
        raise HTTPException(status_code=502, detail="archive failed")
    return {"ok": True, "id": thread_id}


@router.patch("/chat/{bot}/threads/{thread_id}")
async def rename_thread(
    bot: str, thread_id: str,
    payload: dict | None = None,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Rename a thread and lock its title so the auto-titler won't overwrite it."""
    app_state = state(request)
    if bot not in (app_state.adapters or {}):
        raise HTTPException(status_code=404, detail="unknown bot")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="title required")
    raw_title = payload.get("title")
    if not isinstance(raw_title, str) or not raw_title.strip():
        raise HTTPException(status_code=400, detail="title required")
    title = raw_title.strip()[:200]
    vc = _vc_or_404(app_state)
    user_id = _stable_user_id(device.user)
    meta = vc.get_thread(thread_id)
    if meta is None or meta["bot"] != bot or meta["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="unknown thread")
    resp = await vc.thread_rename(thread_id=thread_id, title=title)
    if not resp or not resp.get("ok"):
        raise HTTPException(status_code=502, detail="rename failed")
    return {"ok": True, "id": thread_id, "title": title}


@router.post("/chat/{bot}/threads/{thread_id}/unarchive")
async def unarchive_thread(
    bot: str, thread_id: str,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Restore a previously archived thread."""
    app_state = state(request)
    if bot not in (app_state.adapters or {}):
        raise HTTPException(status_code=404, detail="unknown bot")
    vc = _vc_or_404(app_state)
    user_id = _stable_user_id(device.user)
    meta = vc.get_thread(thread_id)
    if meta is None or meta["bot"] != bot or meta["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="unknown thread")
    resp = await vc.thread_unarchive(thread_id=thread_id)
    if not resp or not resp.get("ok"):
        raise HTTPException(status_code=502, detail="unarchive failed")
    return {"ok": True, "id": thread_id}


@router.post("/chat/{bot}/threads/{thread_id}/pin")
async def pin_thread(
    bot: str, thread_id: str,
    payload: dict | None = None,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Pin (or unpin) a thread so it stays at the top of the list."""
    app_state = state(request)
    if bot not in (app_state.adapters or {}):
        raise HTTPException(status_code=404, detail="unknown bot")
    pinned = True
    if isinstance(payload, dict) and "pinned" in payload:
        pinned = bool(payload["pinned"])
    vc = _vc_or_404(app_state)
    user_id = _stable_user_id(device.user)
    meta = vc.get_thread(thread_id)
    if meta is None or meta["bot"] != bot or meta["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="unknown thread")
    resp = await vc.thread_pin(thread_id=thread_id, pinned=pinned)
    if not resp or not resp.get("ok"):
        raise HTTPException(status_code=502, detail="pin failed")
    return {"ok": True, "id": thread_id, "pinned": pinned}


@router.get("/chat/{bot}/threads/search")
def search_threads(
    bot: str,
    q: str,
    limit: int = 20,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Full-text + title search over threads for (bot, device user).

    Query param ``q`` is the search string. ``limit`` is capped at [1, 100].
    Returns ``{"hits": [{"thread": {...}, "snippet": str}, ...]}``.
    """
    app_state = state(request)
    if bot not in (app_state.adapters or {}):
        raise HTTPException(status_code=404, detail="unknown bot")
    vc = _vc_or_404(app_state)
    user_id = _stable_user_id(device.user)
    hits = vc.search_threads(
        bot=bot, user_id=user_id, query=q,
        limit=max(1, min(int(limit), 100)),
    )
    return {"hits": hits}


@router.post("/chat/{bot}/threads/{thread_id}/fork")
async def fork_thread(
    bot: str, thread_id: str,
    payload: dict | None = None,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Materialise a fork — copy chat_log rows up to (and including)
    the row whose turn_id matches `from_turn_id`. Body shape:
    `{"from_turn_id": "tk-…"}`. If `from_turn_id` is omitted the
    entire source thread is duplicated."""
    app_state = state(request)
    if bot not in (app_state.adapters or {}):
        raise HTTPException(status_code=404, detail="unknown bot")
    vc = _vc_or_404(app_state)
    user_id = _stable_user_id(device.user)
    meta = vc.get_thread(thread_id)
    if meta is None or meta["bot"] != bot or meta["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="unknown thread")
    fork_point = None
    title = None
    if isinstance(payload, dict):
        raw_fp = payload.get("from_turn_id")
        if isinstance(raw_fp, str) and raw_fp.strip():
            fork_point = raw_fp.strip()
        raw_t = payload.get("title")
        if isinstance(raw_t, str) and raw_t.strip():
            title = raw_t.strip()[:200]
    new_id = _new_thread_id()
    resp = await vc.thread_fork(
        new_thread_id=new_id, source_thread_id=thread_id,
        bot=bot, user_id=user_id, title=title,
        fork_point_turn_id=fork_point,
    )
    if not resp or not resp.get("ok"):
        raise HTTPException(
            status_code=502,
            detail=(resp or {}).get("error", "fork failed"),
        )
    return {
        "id": new_id, "parent_thread_id": thread_id,
        "fork_point_turn_id": fork_point,
        "rows_copied": int(resp.get("rows_copied", 0)),
    }


@router.post("/chat/{bot}/turns/{turn_id}/pin")
async def pin_chat_turn(
    bot: str, turn_id: str,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Pin a chat turn: flip chat_log.pinned=1 AND write a markdown
    note into vault/journals/<date>.md so the turn survives a reset
    or thread archive. The pin in chat_log keeps it surfaced in
    sidebar UIs; the journal entry is the durable copy that flows
    into the vault's main FTS index alongside notes/canon."""
    app_state = state(request)
    if bot not in (app_state.adapters or {}):
        raise HTTPException(status_code=404, detail="unknown bot")
    vc = _vc_or_404(app_state)
    user_id = _stable_user_id(device.user)
    rows = vc.get_chat_turn(turn_id)
    if not rows:
        raise HTTPException(status_code=404, detail="unknown turn")
    # Ownership check.
    if rows[0]["bot"] != bot or rows[0]["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="unknown turn")
    # Flip the pin first; the journal write is best-effort below.
    resp = await vc.chat_pin(
        turn_id=turn_id, bot=bot, user_id=user_id, pinned=True,
    )
    if not resp or not resp.get("ok"):
        raise HTTPException(status_code=502, detail="pin failed")
    # Compose the journal body. Keep it short — the chat_log row is
    # the source of truth; the journal is a discoverable index entry.
    parts: list[str] = []
    for r in rows:
        role = "User" if r["role"] == "user" else "Hive"
        parts.append(f"**{role}:** {r['content']}")
    body = "\n\n".join(parts)
    title = f"Pinned turn {turn_id}"
    if hasattr(vc, "learn"):
        # Clamp audience: a device can NEVER pin a turn into a wider
        # vault scope than its own audience permits. Without this a
        # `scout`-only device could pin chat content into the
        # `claude-code` corpus. Single source of truth lives in
        # `shared.audience`.
        from shared.audience import clamp_audience
        device_audience = list(getattr(device, "audience", None) or [])
        audience = clamp_audience([bot, "claude-code"], device_audience)
        try:
            await vc.learn(
                category="knowledge", title=title, body=body,
                author=bot, audience=audience,
                tags=["pinned-turn"],
                extra={"turn_id": turn_id, "thread_id": rows[0]["thread_id"]},
            )
        except Exception as e:  # noqa: BLE001
            log.warning("pin journal write failed: %s", e)
    return {"ok": True, "turn_id": turn_id, "rows": int(resp.get("rows", 0))}


# M1: Legacy bot redirect. Maggy and Scout are gone; their roles are
# folded into Hive (with the M2 hive's Coder + Sysmon helpers). When a
# paired phone or watch hits the old URL, accept the WS, emit a one-shot
# system notice, then proxy through Hive's adapter so the user keeps
# chatting without re-pairing.
_LEGACY_BOTS = {"maggy", "scout"}
_LEGACY_REDIRECT_NOTICE = (
    "Maggy and Scout have been folded into Hive. Continuing as Hive."
)


@router.websocket("/chat/{bot}")
async def chat_ws(websocket: WebSocket, bot: str) -> None:
    from gateway.routes.chat_dispatcher import _ChatDispatcher

    app_state = state(websocket)
    legacy_redirect = bot in _LEGACY_BOTS
    if legacy_redirect:
        bot = "hive"
    adapter = app_state.adapters.get(bot)
    if adapter is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="unknown bot")
        return

    device = await authenticate_ws(websocket, app_state)
    if device is None:
        return

    await websocket.accept()

    if legacy_redirect:
        await websocket.send_json({
            "type": "system_notice",
            "text": _LEGACY_REDIRECT_NOTICE,
        })

    # Thread routing: optional ?thread_id=<id> query param. Defaults to
    # "default" so every pre-Phase-2 client keeps working unchanged.
    # Reject obviously malformed IDs (non-printable, too long) so a
    # broken client can't fan out memory writes across a billion paths.
    raw_thread_id = websocket.query_params.get("thread_id", "default")
    thread_id = _validate_thread_id(raw_thread_id)

    user_name = getattr(device, "name", None) or getattr(device, "id", "")

    dispatcher = _ChatDispatcher(
        websocket=websocket,
        bot=bot,
        device=device,
        app_state=app_state,
        thread_id=thread_id,
        user_name=user_name,
    )

    try:
        await dispatcher.run()
    except WebSocketDisconnect:
        return
    finally:
        # Stale per-device pending state must not survive an abrupt
        # disconnect — otherwise a reconnect would inherit a phantom
        # confirm or reference image from the prior session.
        for slot in ("pending_image_confirms", "pending_image_refs"):
            d = getattr(app_state, slot, None)
            if isinstance(d, dict):
                d.pop(device.id, None)
