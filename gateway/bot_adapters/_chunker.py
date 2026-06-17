"""Post-hoc chunker that streams a completed LLM reply token-ish chunks.

Real token streaming lives in Week 2 once we plumb ollama's ``stream=True``
through LLMClient. For now this gives clients a visible "typing" effect
without touching shared code.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator


async def stream_chunks(
    text: str,
    *,
    chunk_chars: int = 24,
    delay_seconds: float = 0.02,
) -> AsyncIterator[str]:
    if not text:
        return
    for i in range(0, len(text), chunk_chars):
        yield text[i : i + chunk_chars]
        if delay_seconds:
            await asyncio.sleep(delay_seconds)
