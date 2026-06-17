"""Claude Code subprocess manager tests.

We don't spawn the real CLI. Instead we fake the subprocess at the
``asyncio.create_subprocess_exec`` boundary so we can drive stream-json
events deterministically.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from gateway.claude_code import ClaudeCodeManager


class _FakeStdout:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)


class _FakeStdin:
    def __init__(self) -> None:
        self.buf = bytearray()

    def write(self, data: bytes) -> None:
        self.buf.extend(data)

    async def drain(self) -> None:
        return


class _FakeProc:
    def __init__(self, lines: list[bytes]) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(lines)
        self.stderr = _FakeStdout([])
        self.returncode: int | None = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


def _events_to_lines(events: list[dict]) -> list[bytes]:
    return [(json.dumps(e) + "\n").encode("utf-8") for e in events]


@pytest.mark.asyncio
async def test_manager_streams_assistant_and_result(tmp_path: Path, monkeypatch) -> None:
    events = [
        {"type": "system", "session_id": "sess-123"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi there"}]}},
        {"type": "result", "result": "all done"},
    ]
    fake = _FakeProc(_events_to_lines(events))

    async def _spawn(*args, **kwargs) -> _FakeProc:
        return fake

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)

    manager = ClaudeCodeManager(claude_cli_path="/fake/claude")
    collected: list[dict] = []
    async for event in manager.send(
        device_id="dev-a",
        project_path=tmp_path,
        user_text="hello",
    ):
        collected.append(event)

    assert any(e.get("type") == "assistant" for e in collected)
    assert any(e.get("type") == "result" for e in collected)
    # Session ID was captured for resume.
    assert ("dev-a", str(tmp_path)) in manager._sessions
    sess = manager._sessions[("dev-a", str(tmp_path))]
    assert sess.session_id == "sess-123"

    # Input was written with the correct envelope.
    assert b'"type": "user"' in bytes(fake.stdin.buf)
    assert b"hello" in bytes(fake.stdin.buf)

    await manager.close_all()


@pytest.mark.asyncio
async def test_manager_no_cli_returns_error(tmp_path: Path) -> None:
    # Force "no CLI available" even if the test host has claude on PATH.
    manager = ClaudeCodeManager(claude_cli_path="/does/not/exist")
    manager._cli = None
    events: list[dict] = []
    async for e in manager.send(
        device_id="d", project_path=tmp_path, user_text="x",
    ):
        events.append(e)
    assert events and events[0]["type"] == "error"


@pytest.mark.asyncio
async def test_manager_evicts_over_cap(tmp_path: Path, monkeypatch) -> None:
    def _proc_factory() -> _FakeProc:
        return _FakeProc(_events_to_lines([
            {"type": "result", "result": "done"},
        ]))

    async def _spawn(*args, **kwargs) -> _FakeProc:
        return _proc_factory()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)

    manager = ClaudeCodeManager(claude_cli_path="/fake/claude", max_concurrent=1)
    async for _ in manager.send(
        device_id="a", project_path=tmp_path, user_text="1",
    ):
        pass
    async for _ in manager.send(
        device_id="b", project_path=tmp_path, user_text="2",
    ):
        pass
    # Only the most recent key should remain.
    keys = set(manager._sessions.keys())
    assert ("b", str(tmp_path)) in keys
    assert ("a", str(tmp_path)) not in keys
    await manager.close_all()
