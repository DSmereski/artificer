"""Embedding-based contradiction detector for entity_page_update.

When the synthesizer rewrites an entity's `compiled_truth`, this
checks whether the new text is semantically far from the prior. A
large cosine-distance jump (similarity below the threshold) combined
with a negation cue in the new text is the signal we treat as a
contradiction.

When flagged, a journal entry tagged `contradiction` is written via
`vault_learn` so it surfaces in the Activity tab.

Conservative by design: off by default behind
`Config.feature_contradiction_detection`. Best-effort everywhere — a
flaky Ollama / vault daemon never breaks the entity_page_update path.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable

import httpx


log = logging.getLogger("gateway.contradiction_detector")

_DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
_EMBED_MODEL = "nomic-embed-text"
_SIM_FLAG_BELOW = 0.6
_NEGATION_HINTS = (
    " not ", " no longer ", " never ", "actually", "isn't", "wasn't",
    "doesn't", "didn't", "won't", "can't", "incorrect", "wrong",
    "instead", "rather than",
)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _has_negation_cue(text: str) -> bool:
    t = " " + text.lower() + " "
    return any(cue in t for cue in _NEGATION_HINTS)


class EntityContradictionDetector:
    """Wired through ActionExecutor when
    `Config.feature_contradiction_detection` is on. Otherwise stays
    None and `_entity_page_update` skips the check entirely."""

    def __init__(
        self,
        *,
        vault_client_factory: Callable[[], Any],
        ollama_url: str = _DEFAULT_OLLAMA_URL,
        embed_model: str = _EMBED_MODEL,
        sim_threshold: float = _SIM_FLAG_BELOW,
    ) -> None:
        self._vc_factory = vault_client_factory
        self._ollama_url = ollama_url
        self._embed_model = embed_model
        self._sim_threshold = sim_threshold

    async def _embed(self, text: str) -> list[float] | None:
        from shared.embeddings import embed_text
        return await embed_text(
            text,
            ollama_url=self._ollama_url,
            model=self._embed_model,
        )

    async def check(
        self,
        *,
        slug: str,
        title: str,
        prior: str,
        new: str,
        bot: str = "terry",
        device_audience: list[str] | None = None,
    ) -> bool:
        """Return True iff a contradiction was flagged. Best-effort —
        embed/RPC failures return False without raising."""
        prior = (prior or "").strip()
        new = (new or "").strip()
        if not prior or not new or prior == new:
            return False
        # Cheap text gate first — extensions/refinements rarely contain
        # negation words, so we save the embed round-trip on those.
        if not _has_negation_cue(new):
            return False
        prior_vec = await self._embed(prior)
        new_vec = await self._embed(new)
        if prior_vec is None or new_vec is None:
            return False
        sim = _cosine(prior_vec, new_vec)
        if sim >= self._sim_threshold:
            return False
        try:
            client = self._vc_factory()
            await client.learn(
                category="knowledge",
                title=f"Contradiction flagged: {title}",
                body=(
                    f"Entity `{slug}` has a contradicting compiled_truth "
                    f"update.\n\n"
                    f"**Prior:**\n{prior[:500]}\n\n"
                    f"**New:**\n{new[:500]}\n\n"
                    f"Cosine similarity: {sim:.3f} "
                    f"(threshold: {self._sim_threshold})."
                ),
                author=bot,
                audience=device_audience or ["terry", "claude-code"],
                tags=["contradiction", "entity"],
            )
        except Exception as e:  # noqa: BLE001
            log.warning("contradiction journal write failed: %s", e)
            return False
        log.info("contradiction flagged for %s (sim=%.3f)", slug, sim)
        return True
