"""BotAdapter protocol.

Every bot exposed through the gateway implements this interface. The
gateway routes unknown bot names to a 404; everything else goes through
`reply_stream()` which yields text chunks.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol


class BotAdapter(Protocol):
    name: str
    display_name: str

    async def reply_stream(self, user_id: int, text: str) -> AsyncIterator[str]:
        """Yield chunks of a reply to `text` for `user_id`.

        Implementations are expected to also persist history the same way
        the legacy Discord bots do (so conversations started in Discord
        continue in the app and vice versa).
        """
        ...

    def status(self) -> str:
        """Return 'online' | 'loading' | 'offline' | 'error: <msg>'."""
        ...
