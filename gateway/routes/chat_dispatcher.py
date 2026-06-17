"""Per-connection WebSocket dispatch surface extracted from chat_ws().

The _ChatDispatcher class owns the message routing that was previously
inlined inside the `while True` receive loop in chat_ws(). One instance
is created per accepted WebSocket connection; call `await dispatcher.run()`
to enter the loop.

Message kinds handled:
  user / terry / pending-confirm  — routed by _handle_terry_confirm
  user / terry / hive             — routed by _handle_terry_hive
  user / other-bot / streaming    — routed by _handle_other_bot

All logic is preserved line-for-line from the original; only the
structural envelope (class vs. inline loop) changed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

log = logging.getLogger("gateway.chat")


@dataclass
class _QueuedMessage:
    text: str
    user_id: int
    ref_id: str | None
    arrived_ts: float
    queued_while_busy: bool


def _format_queue_marker(
    arrived_ts: float, pending_after_this: int,
) -> str:
    """Build the inline note the model sees on a queued-during-busy msg.

    Kept short — it's prepended to the user's actual text and we want
    the model to handle it as transient meta, not as content to repeat
    back. The pending count helps the planner pick a terser strategy
    when the user is typing faster than we can answer.
    """
    age_s = max(0.0, time.time() - arrived_ts)
    extras = ""
    if pending_after_this:
        extras = f" {pending_after_this} more message(s) are queued behind this one."
    return (
        f"[SYSTEM NOTE: this message arrived about {int(age_s)}s ago while "
        f"you were finishing the previous turn.{extras} "
        f"Answer this message now; the user is aware the prior reply may "
        f"already be on the way.]"
    )


class _ChatDispatcher:
    """Per-WebSocket-connection dispatch surface.

    Owns the message routing table previously inlined into chat_ws().
    Not exported — callers (chat_ws) import it directly from this module.
    """

    def __init__(
        self,
        *,
        websocket: WebSocket,
        bot: str,
        device,
        app_state,
        thread_id: str,
        user_name: str,
    ) -> None:
        self._ws = websocket
        self._bot = bot
        self._device = device
        self._app_state = app_state
        self._thread_id = thread_id
        self._user_name = user_name
        # Async-input queue. recv_loop pushes; process_loop drains.
        # Decouples WS reads from per-turn LLM work so the client can
        # keep typing while a slow turn is in flight.
        self._queue: asyncio.Queue[_QueuedMessage] = asyncio.Queue()
        # True while process_loop is mid-turn. Used by recv_loop to
        # stamp incoming messages with "arrived during a busy turn"
        # so the model later sees a [SYSTEM NOTE] marker.
        self._busy: bool = False
        # Latched by either loop to terminate the other when the WS
        # closes or an unrecoverable error fires.
        self._shutdown = asyncio.Event()

    # ---------------------------------------------------------------- main loop

    async def run(self) -> None:
        """Spawn recv and process loops in parallel.

        recv_loop: drains the WS frame-by-frame into self._queue. Never
        blocks on per-turn LLM work, so the user can pipeline messages.
        process_loop: pulls one message at a time from the queue and
        runs it through the existing _handle_user_message path. Per-
        message ordering is preserved (FIFO).
        """
        recv = asyncio.create_task(self._recv_loop(), name="chat_recv")
        proc = asyncio.create_task(
            self._process_loop(), name="chat_process",
        )
        recv_exc: BaseException | None = None
        try:
            # Wait for recv to terminate (disconnect or error). Then
            # let process drain any items recv enqueued before exit so
            # behaviour matches the legacy synchronous loop: every
            # message recv handed off gets a reply attempt.
            try:
                await recv
            except BaseException as e:  # noqa: BLE001
                recv_exc = e
            self._shutdown.set()
            # Wait for process to drain the queue, with a generous cap
            # so a stuck LLM call doesn't wedge teardown.
            try:
                await asyncio.wait_for(proc, timeout=300.0)
            except asyncio.TimeoutError:
                log.warning("process loop did not drain within timeout; cancelling")
                proc.cancel()
                try:
                    await proc
                except (asyncio.CancelledError, WebSocketDisconnect):
                    pass
            except (asyncio.CancelledError, WebSocketDisconnect):
                pass
            except Exception:  # noqa: BLE001
                log.exception("process loop teardown error")
            if recv_exc is not None:
                raise recv_exc
        finally:
            self._shutdown.set()
            for t in (recv, proc):
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, WebSocketDisconnect):
                        pass
                    except Exception:  # noqa: BLE001
                        log.exception("chat loop teardown error")

    async def _recv_loop(self) -> None:
        """Drain WS frames into the per-message queue.

        Sends back a `{type: "queued"}` ack with the current queue depth
        so the UI can show a "waiting" indicator. Validation errors are
        echoed inline; they don't break the loop.
        """
        try:
            while not self._shutdown.is_set():
                raw = await self._ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await self._ws.send_json(
                        {"type": "error", "message": "invalid json"},
                    )
                    continue
                if not isinstance(msg, dict) or msg.get("type") != "user":
                    await self._ws.send_json(
                        {"type": "error", "message": "expected type=user"},
                    )
                    continue
                text = str(msg.get("text", "")).strip()
                user_id = int(msg.get("user_id", 0)) or _stable_user_id(
                    self._device.user,
                )
                if not text:
                    await self._ws.send_json(
                        {"type": "error", "message": "empty text"},
                    )
                    continue
                ref_id = msg.get("reference_media_id")
                if isinstance(ref_id, str) and ref_id:
                    refs = self._app_state.pending_image_refs
                    if refs is not None:
                        refs[self._device.id] = ref_id
                # "Queued while busy" means EITHER process_loop is
                # currently mid-turn OR there's already a backlog in
                # the queue. The OR catches the race where recv reads
                # message 2 between process_loop's dequeue of message
                # 1 and the _busy=True assignment — without it, msg 2
                # would slip through without a marker.
                queued = _QueuedMessage(
                    text=text,
                    user_id=user_id,
                    ref_id=ref_id if isinstance(ref_id, str) else None,
                    arrived_ts=time.time(),
                    queued_while_busy=self._busy or not self._queue.empty(),
                )
                await self._queue.put(queued)
                if queued.queued_while_busy or self._queue.qsize() > 1:
                    # Only ack when the user is genuinely waiting. A
                    # message arriving while idle gets straight into a
                    # turn and doesn't need a "queued" ack.
                    try:
                        await self._ws.send_json({
                            "type": "queued",
                            "position": self._queue.qsize(),
                        })
                    except Exception:
                        log.debug("queued ack send skipped", exc_info=True)
        except WebSocketDisconnect:
            self._shutdown.set()
            raise

    async def _process_loop(self) -> None:
        """Consume queued messages one at a time and run the hive turn.

        Drains the queue even after shutdown is signaled so a message
        that recv_loop enqueued just before the WS closed still gets
        processed — preserves the legacy behaviour where receiving a
        message and getting a turn result were inseparable.
        """
        while True:
            if self._shutdown.is_set() and self._queue.empty():
                return
            try:
                item = await asyncio.wait_for(
                    self._queue.get(), timeout=0.1,
                )
            except asyncio.TimeoutError:
                continue
            text = item.text
            if item.queued_while_busy:
                # Pending count AT DEQUEUE TIME — the model sees how
                # much is still backed up behind this one so it can
                # pick a terser strategy.
                pending = self._queue.qsize()
                text = (
                    _format_queue_marker(item.arrived_ts, pending)
                    + "\n\n"
                    + text
                )
            self._busy = True
            try:
                turn_id = await self._handle_user_message(
                    text=text, user_id=item.user_id,
                )
                done_frame: dict = {"type": "done"}
                if turn_id:
                    done_frame["turn_id"] = turn_id
                if self._ws.client_state == WebSocketState.CONNECTED:
                    await self._ws.send_json(done_frame)
            except WebSocketDisconnect:
                self._shutdown.set()
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("chat reply failed")
                if self._ws.client_state == WebSocketState.CONNECTED:
                    try:
                        await self._ws.send_json({
                            "type": "error", "message": f"internal: {e}",
                        })
                    except Exception:
                        log.exception("failed to send error frame to client")
            finally:
                self._busy = False

    # ---------------------------------------------------------------- dispatch

    async def _handle_user_message(
        self, *, text: str, user_id: int,
    ) -> str | None:
        """Top-level per-message router. Does NOT send the trailing `done` frame.

        Returns the coordinator-assigned `turn_id` when the message went
        through the hive coordinator; None otherwise (pending-confirm
        branches, non-terry streaming bots).
        """
        if self._bot == "terry":
            return await self._handle_terry(text=text, user_id=user_id)
        await self._handle_other_bot(text=text, user_id=user_id)
        return None

    async def _handle_terry(self, *, text: str, user_id: int) -> str | None:
        """Route a terry-bound message: pending-confirm check, then hive.

        Returns the hive turn_id, or None when the message was consumed
        by the pending-confirm branch (which never runs a hive turn).
        """
        # Pending-confirm hand-off: did Terry just propose a payload,
        # and is this user message a yes/no on it?
        pending = self._app_state.pending_image_confirms
        if pending is not None and self._device.id in pending:
            handled = await self._handle_terry_confirm(text=text, pending=pending)
            if handled:
                return None
            # Anything else: drop pending, treat as a new turn.
            pending.pop(self._device.id, None)

        return await self._handle_terry_hive(text=text, user_id=user_id)

    async def _handle_terry_confirm(self, *, text: str, pending: dict) -> bool:
        """Handle a pending-confirm yes/no response.

        Returns True if the message was consumed (yes or no), False if
        the caller should drop the confirm and continue to the hive path.
        """
        from gateway.conversation_markers import (
            confirmation_yes,
            confirmation_no,
        )
        if confirmation_yes(text):
            kwargs = pending.pop(self._device.id)
            await _render_and_stream(
                self._ws, self._app_state, kwargs,
                device_id=self._device.id,
            )
            return True
        if confirmation_no(text):
            pending.pop(self._device.id, None)
            await self._ws.send_json({
                "type": "assistant", "seq": 0,
                "text": "Cancelled. What did you want to change?",
            })
            return True
        return False

    async def _handle_terry_hive(
        self, *, text: str, user_id: int,
    ) -> str | None:
        """Dispatch a terry message through the hive coordinator.

        Returns the turn_id when the coordinator ran the turn, or None
        when the coordinator was missing (test-only misconfig branch).
        """
        coord = self._app_state.hive_coordinator
        if coord is not None:
            # Look up this device's audience for action-clamping.
            dev_aud: list[str] | None = None
            try:
                for d in self._app_state.devices.list_active():
                    if d.id == self._device.id:
                        dev_aud = list(d.audience or [])
                        break
            except Exception:  # noqa: BLE001
                dev_aud = None
            # Race the hive turn against a disconnect-watch task. If the
            # WS drops mid-turn we cancel the whole turn so vault writes /
            # image renders / ntfy pushes don't fire after the user is gone.
            return await _run_hive_turn_cancel_on_disconnect(
                self._ws, self._app_state,
                coord=coord,
                user_id=user_id, text=text,
                device_id=self._device.id, device_audience=dev_aud,
                thread_id=self._thread_id,
            )
        # Hive coordinator missing — every production boot wires it
        # (gateway/app.py builds it before adapters), so this branch
        # only fires in test configs. Surface the misconfig to the
        # client rather than silently dropping the turn.
        await self._ws.send_json({
            "type": "error",
            "message": "hive coordinator not configured",
        })
        return None

    async def _handle_other_bot(self, *, text: str, user_id: int) -> None:
        """Streaming path for non-terry bots (Maggy / Scout / Claude Code).

        Pulls vault context for non-trivial turns so they can ground
        answers in `knowledge/` notes instead of only their canon + training.
        """
        from gateway import image_research as _ir
        app_state = self._app_state
        adapter = app_state.adapters.get(self._bot)
        cfg = app_state.config
        extra = await _ir.gather_chat_context(
            user_text=text,
            user_name=self._user_name,
            vault_path=cfg.vault_path,
            daemon_host=cfg.vault_writer.host,
            daemon_port=cfg.vault_writer.port,
            agent=self._bot,
        )
        seq = 0
        async for chunk in adapter.reply_stream(
            user_id=user_id, text=text, extra_system=extra,
        ):
            await self._ws.send_json({
                "type": "assistant", "seq": seq, "text": chunk,
            })
            seq += 1


# ---------------------------------------------------------------- thin re-exports
# Keep the dispatcher self-contained: import the helpers it needs from
# chat.py at call time (circular-import safe because chat.py is the caller,
# not the callee).  The two helpers below are defined in chat.py; we reference
# them by name so this module stays importable standalone and the import only
# resolves at call time when chat.py is already loaded.

def _stable_user_id(seed: str) -> int:  # noqa: D401
    """Delegating shim — resolved lazily to avoid circular imports."""
    from gateway.routes.chat import _stable_user_id as _impl
    return _impl(seed)


def _render_and_stream(websocket, app_state, kwargs, *, device_id):
    """Delegating shim — resolved lazily to avoid circular imports."""
    from gateway.routes.chat import _render_and_stream as _impl
    return _impl(websocket, app_state, kwargs, device_id=device_id)


def _run_hive_turn_cancel_on_disconnect(websocket, app_state, **kwargs):
    """Delegating shim — resolved lazily to avoid circular imports."""
    from gateway.routes.chat import _run_hive_turn_cancel_on_disconnect as _impl
    return _impl(websocket, app_state, **kwargs)
