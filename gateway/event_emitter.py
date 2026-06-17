"""WebSocket event emitter for the visible reasoning trace (M2.4).

The HiveCoordinator emits structured events as it works:
  - thought       — Planner's first-step reasoning
  - delegate      — A helper is being dispatched
  - helper_reply  — A helper finished
  - synthesis     — Final synthesizer's plan
  - assistant     — User-facing reply
  - system_notice — Out-of-band notice (legacy redirect, errors, etc.)

`EventEmitter` is a thin wrapper around the WebSocket so tests can
substitute a list-recording emitter.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Protocol


@dataclass
class HiveEvent:
    type: str
    ts: float = field(default_factory=time.time)
    id: str | None = None
    parent: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class EventEmitter(Protocol):
    """Anything that can record a HiveEvent."""

    def thought(
        self, *, summary: str, delegations: list[dict],
        model: str, latency_ms: int, tokens: int,
        id: str | None = None, parent: str | None = None,
    ) -> None: ...

    def delegate(
        self, *, role: str, goal: str, model: str,
        id: str | None = None, parent: str | None = None,
    ) -> None: ...

    def helper_reply(
        self, result, *, id: str | None = None, parent: str | None = None,
    ) -> None: ...

    def helper_late(
        self, result, *, id: str | None = None, parent: str | None = None,
    ) -> None: ...

    def synthesis(
        self, *, summary: str, actions: list[dict],
        parent_id: str | None = None,
    ) -> None: ...

    def assistant(
        self, text: str, *, parent_id: str | None = None,
    ) -> None: ...

    def system_notice(self, text: str) -> None: ...


# ---------------------------------------------------------------- list emitter (tests)


class ListEmitter:
    """Buffer events into an in-memory list. Used by tests."""

    def __init__(self) -> None:
        self.events: list[HiveEvent] = []

    def thought(self, **kw) -> None:
        self.events.append(HiveEvent(
            type="thought",
            id=kw.get("id"), parent=kw.get("parent"),
            payload={
                "summary": kw["summary"],
                "delegations": kw["delegations"],
                "model": kw["model"],
                "latency_ms": kw["latency_ms"],
                "tokens": kw["tokens"],
            },
        ))

    def delegate(self, **kw) -> None:
        self.events.append(HiveEvent(
            type="delegate",
            id=kw.get("id"), parent=kw.get("parent"),
            payload={
                "role": kw["role"], "goal": kw["goal"],
                "model": kw["model"],
            },
        ))

    def helper_reply(self, result, *, id=None, parent=None) -> None:
        self.events.append(HiveEvent(
            type="helper_reply",
            id=id, parent=parent,
            payload={
                "role": result.role, "model_id": result.model_id,
                "plan": result.plan,
                "output_summary": (result.output.get("summary")
                                   if isinstance(result.output, dict) else None),
                "confidence": result.confidence,
                "citations": result.citations,
                "tokens_in": result.tokens_in, "tokens_out": result.tokens_out,
                "latency_ms": result.latency_ms,
                "error": result.error,
            },
        ))

    def helper_late(self, result, *, id=None, parent=None) -> None:
        # Helper completed AFTER synth fired (Phase B / #476). Same shape
        # as helper_reply so downstream tooling can group them; distinct
        # type so dashboards can flag the late tail explicitly.
        self.events.append(HiveEvent(
            type="helper.late",
            id=id, parent=parent,
            payload={
                "role": result.role, "model_id": result.model_id,
                "plan": result.plan,
                "output_summary": (result.output.get("summary")
                                   if isinstance(result.output, dict) else None),
                "confidence": result.confidence,
                "citations": result.citations,
                "tokens_in": result.tokens_in, "tokens_out": result.tokens_out,
                "latency_ms": result.latency_ms,
                "error": result.error,
            },
        ))

    def synthesis(self, *, summary, actions, parent_id=None) -> None:
        self.events.append(HiveEvent(
            type="synthesis",
            parent=parent_id,
            payload={"summary": summary, "actions": actions},
        ))

    def assistant(self, text: str, *, parent_id=None) -> None:
        self.events.append(HiveEvent(
            type="assistant",
            parent=parent_id,
            payload={"text": text},
        ))

    def system_notice(self, text: str) -> None:
        self.events.append(HiveEvent(
            type="system_notice", payload={"text": text},
        ))


# ---------------------------------------------------------------- websocket emitter


_QUIET_TYPES = frozenset({
    "thought", "delegate", "helper_reply", "helper.late", "synthesis",
})


class WebSocketEmitter:
    """Emit events directly to a connected WebSocket, in order.

    Sends are queued through an asyncio.Queue and drained by a
    background flush task. The coordinator's emit calls remain
    synchronous (just enqueue), and the chat handler awaits drain()
    before sending its `done` event so frames arrive in order.
    """

    def __init__(
        self,
        send_json: Callable[[dict], Awaitable[None]],
        loop=None,
        *,
        quiet: bool = False,
    ) -> None:
        import asyncio
        self._send_json = send_json
        self._quiet = quiet
        # `get_running_loop()` is the modern, non-deprecated form. The
        # caller (chat.py:_hive_turn) is async, so a running loop is
        # guaranteed. Tests that construct WebSocketEmitter outside a
        # loop should pass `loop=` explicitly.
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError as e:
                raise RuntimeError(
                    "WebSocketEmitter requires a running event loop "
                    "(or pass loop= explicitly)"
                ) from e
        self._loop = loop
        # Bounded queue so a slow / dead consumer can't make the
        # coordinator buffer megabytes of helper_reply payloads. On
        # overflow `put_nowait` raises QueueFull and `_emit` drops the
        # frame with a logged warning rather than crashing the turn.
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._flush_task = self._loop.create_task(self._flush_loop())
        self._closed = False

    async def _flush_loop(self) -> None:
        import asyncio
        try:
            while True:
                payload = await self._queue.get()
                try:
                    if payload is None:
                        return
                    try:
                        await self._send_json(payload)
                    except Exception:  # noqa: BLE001
                        pass
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            return

    async def drain(self) -> None:
        """Wait for every queued event to flush."""
        await self._queue.join()

    async def close(self) -> None:
        """Drain + stop the flush task. Idempotent — safe to call from
        a `finally:` after a turn raises, even if `close()` already ran
        on the success path. Without idempotency, every disconnected
        turn would leak the flush task on the chat handler's cleanup
        retry."""
        if self._closed:
            return
        self._closed = True
        try:
            await self.drain()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            # Queue full — cancel the task directly. The sentinel
            # would have stopped it cleanly but cancel works too.
            self._flush_task.cancel()
        try:
            await self._flush_task
        except Exception:  # noqa: BLE001
            pass

    def _emit(self, event: HiveEvent) -> None:
        if self._quiet and event.type in _QUIET_TYPES:
            return
        try:
            payload = {"type": event.type, "ts": event.ts}
            if event.id is not None:
                payload["id"] = event.id
            if event.parent is not None:
                payload["parent"] = event.parent
            payload.update(event.payload)
            self._queue.put_nowait(payload)
        except Exception as e:  # noqa: BLE001
            # Don't crash a turn just because we couldn't queue an
            # event — but DO log so silent drops are debuggable.
            import logging
            logging.getLogger("gateway.event_emitter").warning(
                "failed to enqueue %r event: %s", event.type, e,
            )

    def thought(self, **kw) -> None:
        self._emit(HiveEvent(
            type="thought",
            id=kw.get("id"), parent=kw.get("parent"),
            payload={
                "summary": kw["summary"],
                "delegations": kw["delegations"],
                "model": kw["model"],
                "latency_ms": kw["latency_ms"],
                "tokens": kw["tokens"],
            },
        ))

    def delegate(self, **kw) -> None:
        self._emit(HiveEvent(
            type="delegate",
            id=kw.get("id"), parent=kw.get("parent"),
            payload={
                "role": kw["role"], "goal": kw["goal"],
                "model": kw["model"],
            },
        ))

    def helper_reply(self, result, *, id=None, parent=None) -> None:
        self._emit(HiveEvent(
            type="helper_reply",
            id=id, parent=parent,
            payload={
                "role": result.role, "model_id": result.model_id,
                "plan": result.plan,
                "output_summary": (result.output.get("summary")
                                   if isinstance(result.output, dict) else None),
                "confidence": result.confidence,
                "citations": result.citations,
                "tokens_in": result.tokens_in,
                "tokens_out": result.tokens_out,
                "latency_ms": result.latency_ms,
                "error": result.error,
            },
        ))

    def helper_late(self, result, *, id=None, parent=None) -> None:
        # Late completer (#476). See ListEmitter.helper_late.
        self._emit(HiveEvent(
            type="helper.late",
            id=id, parent=parent,
            payload={
                "role": result.role, "model_id": result.model_id,
                "plan": result.plan,
                "output_summary": (result.output.get("summary")
                                   if isinstance(result.output, dict) else None),
                "confidence": result.confidence,
                "citations": result.citations,
                "tokens_in": result.tokens_in,
                "tokens_out": result.tokens_out,
                "latency_ms": result.latency_ms,
                "error": result.error,
            },
        ))

    def synthesis(self, *, summary, actions, parent_id=None) -> None:
        self._emit(HiveEvent(
            type="synthesis",
            parent=parent_id,
            payload={"summary": summary, "actions": actions},
        ))

    def assistant(self, text: str, *, parent_id=None) -> None:
        self._emit(HiveEvent(
            type="assistant",
            parent=parent_id,
            payload={"text": text},
        ))

    def system_notice(self, text: str) -> None:
        self._emit(HiveEvent(
            type="system_notice", payload={"text": text},
        ))
