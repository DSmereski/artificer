"""Claude Code subprocess manager.

Spawns ``claude`` CLI sessions with --print + stream-json output so the
gateway can proxy user turns as streamed assistant text.

Lifecycle:
  * One subprocess per (device, project) — keyed so a forgotten mobile
    session doesn't collide with the desktop session on the same project.
  * Idle-kill after ``IDLE_TIMEOUT_SECONDS``.
  * ``MAX_CONCURRENT`` sessions across the whole gateway; newest-first
    eviction when the cap is hit.

v1 uses the CLI's --resume flag for continuity. v2 will migrate to the
Agent SDK for finer-grained control (tool use events, token counts,
etc.); keeping the interface small (send/stream/close) means adapters
only change behind the curtain.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator


log = logging.getLogger("gateway.claude_code")


CLAUDE_CLI_ENV = "CLAUDE_CLI_PATH"
IDLE_TIMEOUT_SECONDS = 900        # 15 min
MAX_CONCURRENT = 2


def _resolve_claude_cli() -> str | None:
    override = os.environ.get(CLAUDE_CLI_ENV)
    if override and Path(override).exists():
        return override
    found = shutil.which("claude")
    return found


@dataclass
class Session:
    key: tuple[str, str]              # (device_id, project_path)
    cwd: Path
    proc: asyncio.subprocess.Process
    last_used: float = field(default_factory=time.time)
    session_id: str | None = None     # filled in after first turn (from stream-json)


class ClaudeCodeManager:
    """Per-device, per-project Claude Code subprocess pool."""

    def __init__(
        self,
        *,
        claude_cli_path: str | None = None,
        idle_timeout: float = IDLE_TIMEOUT_SECONDS,
        max_concurrent: int = MAX_CONCURRENT,
    ) -> None:
        self._cli = claude_cli_path or _resolve_claude_cli()
        self._idle = idle_timeout
        self._max = max_concurrent
        self._sessions: dict[tuple[str, str], Session] = {}
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self._cli is not None

    # -------------------------------------------------------------- pool

    async def _evict_if_needed(self) -> None:
        # Drop idle sessions first.
        now = time.time()
        for key in list(self._sessions):
            s = self._sessions[key]
            if now - s.last_used > self._idle or s.proc.returncode is not None:
                log.info("claude-code: evicting idle session %s", key)
                await self._close_session_locked(key)
        # Then enforce the hard cap (oldest first).
        if len(self._sessions) >= self._max:
            oldest_key = min(self._sessions, key=lambda k: self._sessions[k].last_used)
            log.info("claude-code: evicting oldest session %s for cap", oldest_key)
            await self._close_session_locked(oldest_key)

    async def _close_session_locked(self, key: tuple[str, str]) -> None:
        s = self._sessions.pop(key, None)
        if s is None:
            return
        if s.proc.returncode is None:
            try:
                s.proc.terminate()
                try:
                    await asyncio.wait_for(s.proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    s.proc.kill()
            except ProcessLookupError:
                pass

    async def close_all(self) -> None:
        async with self._lock:
            for key in list(self._sessions):
                await self._close_session_locked(key)

    # -------------------------------------------------------------- send

    async def send(
        self,
        *,
        device_id: str,
        project_path: Path,
        user_text: str,
        plan_mode: bool = False,
    ) -> AsyncIterator[dict]:
        """Send ``user_text`` to a Claude Code session for ``(device, project)``.

        Yields dicts of {"type": "assistant"|"tool_use"|"result"|"error", ...}
        parsed from the CLI's ``--output-format stream-json``.
        """
        if not self.available:
            yield {"type": "error", "message": "claude CLI not found on PATH"}
            return

        key = (device_id, str(project_path))

        async with self._lock:
            await self._evict_if_needed()
            session = self._sessions.get(key)
            if session is None or session.proc.returncode is not None:
                session = await self._spawn(key, project_path, plan_mode)
                self._sessions[key] = session

        session.last_used = time.time()

        # Each user turn: write a stream-json input line, then read output
        # until we hit a "result" event (end of turn) or EOF.
        input_event = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": user_text},
        })
        try:
            session.proc.stdin.write((input_event + "\n").encode("utf-8"))
            await session.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as e:
            yield {"type": "error", "message": f"write failed: {e}"}
            async with self._lock:
                await self._close_session_locked(key)
            return

        while True:
            line = await session.proc.stdout.readline()
            if not line:
                yield {"type": "error", "message": "session ended unexpectedly"}
                async with self._lock:
                    await self._close_session_locked(key)
                return
            try:
                event = json.loads(line.decode("utf-8").strip())
            except json.JSONDecodeError:
                continue
            # Track the CLI's session id for resume-on-reconnect.
            if session.session_id is None:
                sid = event.get("session_id") or event.get("sessionId")
                if sid:
                    session.session_id = str(sid)
            yield event
            if event.get("type") == "result":
                return

    async def _spawn(
        self, key: tuple[str, str], project_path: Path, plan_mode: bool,
    ) -> Session:
        permission_mode = "plan" if plan_mode else "acceptEdits"
        args = [
            self._cli,
            "--print",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--permission-mode", permission_mode,
            "--verbose",
        ]
        # Resume an existing session if we've seen one for this key before.
        prev = self._sessions.get(key)
        if prev is not None and prev.session_id:
            args.extend(["--resume", prev.session_id])

        log.info("claude-code: spawning %s in %s (plan=%s)",
                 args, project_path, plan_mode)

        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(project_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return Session(key=key, cwd=project_path, proc=proc)
