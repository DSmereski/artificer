"""Claude Code adapter — spawns a ``claude`` CLI subprocess per session."""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator


class ClaudeCodeAdapter:
    name = "claude-code"
    display_name = "Claude Code"

    def __init__(self, manager, default_project: Path) -> None:
        # manager: gateway.claude_code.ClaudeCodeManager
        self._manager = manager
        self._default_project = default_project

    def status(self) -> str:
        if not self._manager.available:
            return "error: claude CLI not found on PATH"
        return "online"

    async def reply_stream(
        self, user_id: int, text: str, *, extra_system: str = "",
    ) -> AsyncIterator[str]:
        # extra_system is ignored — the claude CLI subprocess has its own
        # context window and we don't have a clean way to inject vault notes
        # into a long-lived subprocess. Accept the kwarg so the chat route
        # can call all adapters uniformly.
        del extra_system
        if not self._manager.available:
            yield (
                "Claude Code CLI isn't on PATH. Install Claude Code or set the "
                "CLAUDE_CLI_PATH env var and restart the gateway."
            )
            return

        device_id = f"user-{user_id}"
        project = self._default_project

        async for event in self._manager.send(
            device_id=device_id,
            project_path=project,
            user_text=text,
        ):
            for chunk in self._flatten(event):
                yield chunk

    @staticmethod
    def _flatten(event: dict) -> list[str]:
        """Extract user-visible text from one stream-json event."""
        t = event.get("type")
        if t == "assistant":
            msg = event.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                return [content]
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        parts.append(str(block.get("text", "")))
                return parts
        elif t == "tool_use":
            tool = event.get("name") or (event.get("tool_use") or {}).get("name") or "tool"
            return [f"\n[tool: {tool}]\n"]
        elif t == "result":
            res = event.get("result")
            if isinstance(res, str):
                return [f"\n{res}"]
        elif t == "error":
            return [f"\n[error: {event.get('message', 'unknown')}]\n"]
        return []
