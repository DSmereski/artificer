"""Hive adapter — text half of Hive (voice is a separate WS route)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

from shared.llm_client import LLMClient, _DEFAULT_SYSTEM
from shared.vault_client import VaultClient

from gateway.image_catalog import ImageCatalog, pointer_catalog_block

from ._canon import CanonLoader
from ._chunker import stream_chunks


def _hive_base_prompt(catalog: ImageCatalog | None) -> str:
    """Hive's base = LLMClient default + a short LoRA-catalog pointer.

    The pointer (~10 lines) replaces the old full catalog dump (~1500 chars).
    Detailed LoRA info lives in the vault note `canon/imagegen-loras.md` and
    is also surfaced via proactive vault search at request time. Canon still
    splices on top of this base via CanonLoader._apply.
    """
    block = pointer_catalog_block(catalog) if catalog else ""
    if not block:
        return _DEFAULT_SYSTEM
    return _DEFAULT_SYSTEM + "\n\n" + block


class HiveAdapter:
    name = "hive"
    # User-visible name shown in the app header / dropdown. Internal
    # `name` stays "hive" for /v1/chat/hive route compat + history
    # paths until a deeper rebrand sweep retires those.
    display_name = "Hive"

    def __init__(
        self,
        history_dir: Path,
        model: str | None = None,
        vault_client: VaultClient | None = None,
        image_catalog: ImageCatalog | None = None,
    ) -> None:
        self._image_catalog = image_catalog
        base = _hive_base_prompt(image_catalog)
        self._llm = LLMClient(system_prompt=base, history_dir=history_dir)
        if model:
            self._llm.MODEL = model

        self._canon: CanonLoader | None = None
        if vault_client is not None:
            self._canon = CanonLoader(
                agent="hive",
                base_prompt=base,
                llm=self._llm,
                vault_client=vault_client,
            )
            self._canon.refresh()

    def status(self) -> str:
        return "online"

    async def refresh_canon(self) -> int:
        if self._canon is None:
            return 0
        return await self._canon.refresh_async()

    def reload_image_catalog(self, catalog: ImageCatalog) -> None:
        """Swap in a freshly-loaded catalog without restarting the gateway."""
        self._image_catalog = catalog
        base = _hive_base_prompt(catalog)
        if self._canon is not None:
            self._canon.rebuild_with(base)
        else:
            self._llm._system = base

    async def reply(self, user_id: int, text: str, *, extra_system: str = "") -> str:
        """Non-streaming reply, optionally with one-turn extra system context.

        The chat route uses this for Hive so it can scan for `[ASK_USER]` /
        `[CONFIRM_IMAGE]` / `[REMEMBER]` / `[GENERATE_IMAGE]` markers BEFORE
        the user sees them. `extra_system` is image-research vault context —
        applies only to this single turn, not stored in history.

        After the LLM call, the marker-stripped (visible) version is written
        back into history. Persisting raw markers caused Hive to see her
        own previous control codes and treat them as conversational content,
        leading to confused short replies on subsequent turns.
        """
        from gateway.conversation_markers import sanitize_hive_reply
        loop = asyncio.get_running_loop()
        raw_reply = await loop.run_in_executor(
            None, self._llm.chat, user_id, text, extra_system,
        )
        # Sanitize the persisted history entry: keep what the user saw, drop
        # markers AND fake-progress prose Hive sometimes invents (Status:
        # lines, "Initializing render engine...", etc.). Without this, her
        # next turn sees her own fabrication and copies it.
        clean = sanitize_hive_reply(raw_reply)
        history = self._llm._history.get(user_id)
        if history and history[-1].get("role") == "assistant":
            if not clean.strip():
                # Pure marker turn (e.g. just [WEB_LOOKUP] X). Storing
                # this as `""` confused the small model on the follow-up
                # turn ("Hey, I'm here!" instead of synthesising the web
                # result). Drop the empty entry entirely so the next
                # turn sees the natural user→user flow.
                history.pop()
            else:
                history[-1]["content"] = clean
            try:
                self._llm._save(user_id)
            except Exception:  # noqa: BLE001
                pass
        return raw_reply

    async def reply_stream(self, user_id: int, text: str) -> AsyncIterator[str]:
        """Streaming variant. Kept for parity with other adapters; the chat
        route uses .reply() for Hive now to enable marker scanning."""
        loop = asyncio.get_running_loop()
        reply = await loop.run_in_executor(None, self._llm.chat, user_id, text)
        async for chunk in stream_chunks(reply):
            yield chunk
