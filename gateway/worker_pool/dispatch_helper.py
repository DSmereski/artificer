"""Synchronous-feeling dispatch façade for in-process hive helpers.

Hive helpers run on the same asyncio loop as the gateway, so they can
`await` directly on the result of a dispatched job. We keep a per-job
`asyncio.Future` in `Dispatcher._waiters` and resolve it from the
dispatcher's `complete` / `report_adapter_error` / `fail` paths
(see `Dispatcher._notify_waiter`).

Two failure modes:
  - `DispatchTimeout`: the timeout elapsed before any node returned a
    result.
  - `DispatchError`: the runtime adapter reported error, OR the job
    burned through all retry attempts.
"""

from __future__ import annotations

import asyncio
from typing import Any

from gateway.worker_pool.dispatcher import (
    Dispatcher,
    HiveJob,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_FAILED,
)


class DispatchTimeout(Exception):
    """Raised when no node returned a result within timeout_s."""


class DispatchError(Exception):
    """Raised when the job ended in `error` or `failed` state."""


async def dispatch_and_wait(
    dispatcher: Dispatcher,
    *,
    kind: str,
    payload: dict[str, Any],
    required_caps: tuple[str, ...] = (),
    timeout_s: float = 300.0,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Enqueue a job and await its terminal state.

    On success: returns
        {"status": "done", "output": <dict>, "duration_ms": <int>,
         "job_id": <str>, "node_id": <str>}.
    On `status='error'` or `status='failed'`: raises `DispatchError`
    with the adapter's error message.
    On timeout: raises `DispatchTimeout`.
    """
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[HiveJob] = loop.create_future()
    job = dispatcher.enqueue(
        kind=kind,
        payload=payload,
        required_caps=required_caps,
        max_attempts=max_attempts,
    )
    # Register the waiter atomically. If the job somehow finishes before
    # we register (impossible under the GIL since enqueue ran on this
    # thread, but defensive), peek at the current state immediately.
    with dispatcher._waiter_lock:  # noqa: SLF001 — same package
        dispatcher._waiters[job.id] = fut  # noqa: SLF001
    try:
        try:
            done_job = await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError as e:
            raise DispatchTimeout(
                f"job {job.id} ({kind}) timed out after {timeout_s:.0f}s",
            ) from e
    finally:
        with dispatcher._waiter_lock:  # noqa: SLF001
            dispatcher._waiters.pop(job.id, None)  # noqa: SLF001

    if done_job.status == STATUS_DONE:
        return {
            "status": "done",
            "output": done_job.result or {},
            "duration_ms": done_job.duration_ms or 0,
            "job_id": done_job.id,
            "node_id": done_job.node_id or "",
        }
    if done_job.status in (STATUS_ERROR, STATUS_FAILED):
        raise DispatchError(done_job.error or f"job {done_job.id} {done_job.status}")
    raise DispatchError(
        f"job {done_job.id} ended in unexpected status {done_job.status}",
    )
