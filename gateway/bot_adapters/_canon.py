"""Shared vault-canon loader for gateway bot adapters.

The Discord bots (bots/{maggy,terry,scout}/bot.py) splice canon/*.md into their
LLM system prompts via a 30-min tasks.loop. This module is the gateway-side
equivalent so the same bots talking through the app see the same canon.

Usage from an adapter:

    self._canon = CanonLoader(agent="maggy", base_prompt=_MAGGY_SYSTEM,
                              llm=self._llm, vault_client=vc)
    self._canon.refresh()                       # blocking, at startup
    await self._canon.refresh_async()           # from the background task
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from shared.llm_client import LLMClient
from shared.vault_client import VaultClient
from vault_writer.util import wrap_untrusted

log = logging.getLogger("gateway.canon")


@dataclass
class CanonLoader:
    agent: str
    base_prompt: str
    llm: LLMClient
    vault_client: VaultClient
    _cached: str = field(default="", init=False)

    def refresh(self) -> int:
        """Synchronous reload. Returns chars loaded; 0 on failure.

        Never raises — a vault that's down should not break chat.
        """
        try:
            canon = self.vault_client.preload_canon(self.agent)
        except Exception as e:  # noqa: BLE001
            log.warning("canon refresh failed for %s: %s", self.agent, e)
            return 0
        self._cached = canon or ""
        self._apply()
        log.info("canon loaded for %s (%d chars)", self.agent, len(self._cached))
        return len(self._cached)

    async def refresh_async(self) -> int:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.refresh)

    def _apply(self) -> None:
        if self._cached:
            self.llm._system = (
                self.base_prompt
                + "\n\n"
                + wrap_untrusted(self._cached, source="vault")
            )
        else:
            self.llm._system = self.base_prompt

    def rebuild_with(self, new_base: str) -> None:
        """For adapters (like Scout) that re-derive their base prompt per turn.

        Splices the cached canon onto a freshly-built base prompt.
        """
        self.base_prompt = new_base
        self._apply()
