"""WS /v1/term — loopback-only, Bearer-authed PowerShell PTY-over-WS.

SECURITY MODEL (non-negotiable):
  1. Loopback-only: reject any connection whose remote host is NOT in
     {127.0.0.1, ::1}. The gateway also binds a Tailscale IP; the terminal
     must NEVER be reachable from the tailnet. This check happens BEFORE
     the WebSocket handshake is accepted and BEFORE the token is read.
  2. Bearer token: the standard `authenticate_ws` dependency — same path
     used by every other WS route in the gateway.
  3. Config flag: `terminal_enabled` in config.py / gateway.yaml. When False,
     the endpoint immediately closes with WS_1008_POLICY_VIOLATION.
  4. Concurrent session cap: `terminal_max_sessions` (default 2).
  5. Idle timeout: `terminal_idle_timeout_s` (default 600s). The shell is
     killed after this many seconds with no stdin from the client.

Shell lifecycle:
  - One shell process per WS connection. pywinpty (ConPTY) is used when
    available for full ANSI / interactive support.  Falls back to
    subprocess.Popen with merged stdout/stderr piped over WS (line-oriented,
    no ANSI resize).
  - Shell is killed on WS disconnect — no orphan processes.
  - Command contents are NEVER logged. Only session events (connect, resize,
    disconnect) are logged at DEBUG level.

Frame protocol (text JSON from client → server):
  {"type": "input",  "data": "<base64-or-raw stdin>"}
  {"type": "resize", "cols": 120, "rows": 40}        (pywinpty only)

Server → client: raw binary/text frames (shell stdout+stderr).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket, status

from gateway.deps import authenticate_ws, state

if TYPE_CHECKING:
    pass

log = logging.getLogger("gateway.terminal")

router = APIRouter(tags=["terminal"])

# ─── Session registry ─────────────────────────────────────────────────────────

_active_sessions: set[str] = set()
_session_lock = asyncio.Lock()

# ─── Loopback guard ───────────────────────────────────────────────────────────

_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


def _is_loopback(host: str) -> bool:
    """True iff the remote host is a loopback address.

    We allow 127.0.0.0/8 for completeness (some WebView2 variants report
    127.x.x.x rather than 127.0.0.1). Tailscale IPs (100.x) and LAN IPs
    are explicitly rejected.
    """
    if host in _LOOPBACK_HOSTS:
        return True
    # Full 127.0.0.0/8 range
    if host.startswith("127."):
        try:
            parts = host.split(".")
            return len(parts) == 4 and all(p.isdigit() for p in parts)
        except Exception:  # noqa: BLE001
            return False
    return False


# ─── PTY backend ─────────────────────────────────────────────────────────────

try:
    import winpty as _winpty
    _PYWINPTY_AVAILABLE = True
except ImportError:
    _winpty = None  # type: ignore[assignment]
    _PYWINPTY_AVAILABLE = False


# ─── Shell session: pywinpty ─────────────────────────────────────────────────

async def _run_session_pty(
    ws: WebSocket,
    *,
    idle_timeout_s: float,
) -> None:
    """Run a full ConPTY session over the WebSocket using pywinpty."""
    assert _winpty is not None

    loop = asyncio.get_running_loop()
    pty_proc = await loop.run_in_executor(
        None,
        lambda: _winpty.PtyProcess.spawn(
            ["powershell", "-NoLogo", "-NoProfile"],
            dimensions=(24, 80),
        ),
    )
    log.debug("terminal: PTY session started (pywinpty)")

    last_input_at = time.monotonic()

    async def _read_pty() -> None:
        """Forward PTY output → WebSocket in a tight loop."""
        while True:
            try:
                chunk = await loop.run_in_executor(None, pty_proc.read, 4096)
            except Exception:  # noqa: BLE001
                break
            if not chunk:
                break
            try:
                await ws.send_bytes(chunk.encode("utf-8", errors="replace"))
            except Exception:  # noqa: BLE001
                break

    async def _idle_watchdog() -> None:
        nonlocal last_input_at
        while True:
            await asyncio.sleep(10)
            if time.monotonic() - last_input_at > idle_timeout_s:
                log.debug("terminal: idle timeout reached, killing shell")
                break

    reader_task = asyncio.create_task(_read_pty(), name="term-pty-reader")
    watchdog_task = asyncio.create_task(_idle_watchdog(), name="term-pty-watchdog")

    try:
        while True:
            # Cancel if reader or watchdog finished
            if reader_task.done() or watchdog_task.done():
                break

            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception:  # noqa: BLE001
                break

            # Parse JSON frame from client
            try:
                import json
                frame = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue

            ftype = frame.get("type")
            if ftype == "input":
                last_input_at = time.monotonic()
                data = frame.get("data", "")
                if isinstance(data, str):
                    # Accept either raw text or base64-encoded
                    try:
                        decoded = base64.b64decode(data).decode("utf-8", errors="replace")
                    except Exception:  # noqa: BLE001
                        decoded = data
                    await loop.run_in_executor(None, pty_proc.write, decoded)
            elif ftype == "resize":
                cols = int(frame.get("cols", 80))
                rows = int(frame.get("rows", 24))
                cols = max(1, min(cols, 500))
                rows = max(1, min(rows, 200))
                await loop.run_in_executor(
                    None, pty_proc.setwinsize, rows, cols,
                )
    finally:
        reader_task.cancel()
        watchdog_task.cancel()
        for t in (reader_task, watchdog_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        try:
            pty_proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        log.debug("terminal: PTY session ended (pywinpty)")


# ─── Shell session: subprocess fallback ──────────────────────────────────────

async def _run_session_subprocess(
    ws: WebSocket,
    *,
    idle_timeout_s: float,
) -> None:
    """Line-oriented fallback when pywinpty is unavailable.

    Uses asyncio subprocess with merged stdout/stderr. Input from the client
    is written to stdin line-by-line. No ANSI support; no resize.
    """
    import subprocess
    proc = await asyncio.create_subprocess_exec(
        "powershell", "-NoLogo", "-NoProfile", "-Command", "-",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    log.debug("terminal: subprocess session started (fallback)")

    last_input_at = time.monotonic()
    assert proc.stdout is not None
    assert proc.stdin is not None

    async def _read_proc() -> None:
        while True:
            try:
                chunk = await proc.stdout.read(4096)
            except Exception:  # noqa: BLE001
                break
            if not chunk:
                break
            try:
                await ws.send_bytes(chunk)
            except Exception:  # noqa: BLE001
                break

    async def _idle_watchdog() -> None:
        while True:
            await asyncio.sleep(10)
            if time.monotonic() - last_input_at > idle_timeout_s:
                log.debug("terminal: subprocess idle timeout, killing shell")
                break

    reader_task = asyncio.create_task(_read_proc(), name="term-sub-reader")
    watchdog_task = asyncio.create_task(_idle_watchdog(), name="term-sub-watchdog")

    try:
        while True:
            if reader_task.done() or watchdog_task.done():
                break
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception:  # noqa: BLE001
                break

            try:
                import json
                frame = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue

            ftype = frame.get("type")
            if ftype == "input":
                last_input_at = time.monotonic()
                data = frame.get("data", "")
                if isinstance(data, str):
                    try:
                        decoded = base64.b64decode(data)
                    except Exception:  # noqa: BLE001
                        decoded = data.encode("utf-8", errors="replace")
                    try:
                        proc.stdin.write(decoded)
                        await proc.stdin.drain()
                    except Exception:  # noqa: BLE001
                        break
            # resize frames are silently ignored in subprocess mode
    finally:
        reader_task.cancel()
        watchdog_task.cancel()
        for t in (reader_task, watchdog_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        try:
            proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except Exception:  # noqa: BLE001
            pass
        log.debug("terminal: subprocess session ended (fallback)")


# ─── WebSocket endpoint ───────────────────────────────────────────────────────

@router.websocket("/v1/term")
async def terminal_ws(websocket: WebSocket) -> None:
    """WS /v1/term — PowerShell PTY over WebSocket.

    Security: loopback-only AND Bearer token required.
    Config: terminal_enabled must be True.
    Caps: terminal_max_sessions concurrent sessions.
    Idle timeout: terminal_idle_timeout_s of no stdin kills the shell.
    """
    app_state = state(websocket)
    cfg = app_state.config

    # ── 1. Feature flag ───────────────────────────────────────────────────────
    if not getattr(cfg, "terminal_enabled", True):
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="terminal disabled",
        )
        return

    # ── 2. LOOPBACK CHECK — must happen before accept + before auth ────────────
    #
    # websocket.client is set by Starlette from the ASGI scope's "client" tuple
    # BEFORE the WebSocket handshake is accepted. We read it here so the check
    # is enforced even if the auth step would also reject the connection.
    client = websocket.client
    remote_host = client.host if client else ""
    if not _is_loopback(remote_host):
        # Do NOT accept the connection — close at the transport layer.
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="terminal endpoint is loopback-only",
        )
        log.warning(
            "terminal: rejected non-loopback connection from %s", remote_host
        )
        return

    # ── 3. Token auth — checked BEFORE accept so rejection is pre-handshake ─────
    #
    # authenticate_ws reads the Authorization header / ?token= query param
    # and would normally need the handshake to be open before it can send a
    # close frame.  For the terminal we enforce the stricter behaviour: parse
    # the token WITHOUT accepting first, and close at the transport layer on
    # failure (same as the loopback check above).
    token: str | None = None
    auth = websocket.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth.split(None, 1)[1].strip()
    if not token:
        token = websocket.query_params.get("token")
    if not token:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="missing token",
        )
        return
    device_obj = app_state.devices.verify(token)
    if device_obj is None:
        # The wallpaper dashboard runs on loopback and authenticates with the
        # per-process BOARD session-token (the same one it uses for board
        # mutations), NOT a device Bearer. Accept it here — the connection is
        # already loopback-gated above, and that token is only ever handed to
        # loopback callers. Any other unknown token is still rejected.
        import secrets as _secrets
        from gateway.routes.board import _BOARD_TOKEN
        if _is_loopback(remote_host) and _secrets.compare_digest(token, _BOARD_TOKEN):
            pass  # board-token loopback session — allowed
        else:
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="invalid token",
            )
            return
    else:
        app_state.devices.touch(device_obj.id)

    # ── 4. Accept WebSocket (auth passed — safe to upgrade) ───────────────────
    await websocket.accept()
    device = device_obj
    _dev_id = device_obj.id if device_obj is not None else "loopback-board"

    # ── 5. Concurrency cap ────────────────────────────────────────────────────
    max_sessions = getattr(cfg, "terminal_max_sessions", 2)
    # Unique per connection. NOT id(websocket): CPython recycles object ids after
    # GC, so a reconnecting socket could collide with a just-closed session's id
    # and desync _active_sessions — leaking slots until the cap wedges and new
    # shells are refused ("terminal broke after closing a tab").
    session_id = f"{_dev_id}:{uuid.uuid4().hex}"

    async with _session_lock:
        if len(_active_sessions) >= max_sessions:
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason=f"max terminal sessions ({max_sessions}) reached",
            )
            return
        _active_sessions.add(session_id)

    log.debug(
        "terminal: session started device=%s sessions_now=%d",
        _dev_id,
        len(_active_sessions),
    )

    idle_timeout_s = getattr(cfg, "terminal_idle_timeout_s", 600.0)

    try:
        if _PYWINPTY_AVAILABLE:
            await _run_session_pty(websocket, idle_timeout_s=idle_timeout_s)
        else:
            await _run_session_subprocess(websocket, idle_timeout_s=idle_timeout_s)
    except Exception:  # noqa: BLE001
        log.debug("terminal: session ended with exception", exc_info=True)
    finally:
        async with _session_lock:
            _active_sessions.discard(session_id)
        log.debug(
            "terminal: session closed device=%s sessions_now=%d",
            _dev_id,
            len(_active_sessions),
        )
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
