"""Librarian helper — vault search + retrieval.

Real vault search runs **before** the LLM call. If the search returns
zero candidates, we short-circuit with an empty VaultPlan — saving a
20+ second LLM round-trip and avoiding the failure mode where the
model echoes the input envelope back instead of producing a VaultPlan.

When candidates exist, we hand them to the LLM as `inputs.candidates`
so the prompt's "given these candidates" framing matches reality.
"""

from __future__ import annotations

import json
import logging

import httpx

from gateway.helpers.base import (
    BaseHelper, HelperResult, HelperTask, ResultBuilder,
)
from gateway.helpers.shapes import VaultPlan


log = logging.getLogger("gateway.helpers.librarian")


_DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
_EMBED_MODEL = "nomic-embed-text"
_TOP_K = 8
_MAX_BODY_CHARS = 1200          # truncate per-candidate to bound prompt size


class LibrarianHelper(BaseHelper):
    role = "librarian"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("schema", VaultPlan)
        # Optional preflight deps. When absent, librarian degrades to
        # the old behaviour (LLM-only with whatever the planner sent).
        self._vault_client_factory = kwargs.pop("vault_client_factory", None)
        self._ollama_url = kwargs.pop("ollama_url", _DEFAULT_OLLAMA_URL)
        super().__init__(**kwargs)

    async def invoke(self, task: HelperTask) -> HelperResult:
        # Preflight: turn the task into (query, audience, candidates).
        query = self._extract_query(task)
        audience = self._extract_audience(task)
        candidates: list[dict] = []

        if self._vault_client_factory is not None and query:
            try:
                candidates = await self._search_vault(query, audience)
            except Exception as e:  # noqa: BLE001
                log.warning("librarian vault search failed: %s", e)
                candidates = []

        # Short-circuit: zero candidates → return empty plan immediately.
        # The synthesizer's Rule 8 will admit ignorance to the user.
        if not candidates:
            rb = ResultBuilder(
                role=self.role, model_id=self.model_id,
                parent_id=task.parent_id,
            )
            rb.output = {
                "summary": (
                    f"no relevant notes in vault for {query!r}"
                    if query else "no query supplied"
                ),
                "hits": [],
                "plan": ["search vault", "summarize hits"],
            }
            rb.confidence = "low"
            return rb.build()

        # Have candidates — pass them to the LLM via task inputs and let
        # the standard invoke path handle prompt + schema.
        enriched = HelperTask(
            role=task.role,
            goal=task.goal,
            inputs={
                "query": query,
                "candidates": candidates,
            },
            constraints=task.constraints,
            expected_schema=task.expected_schema,
            parent_id=task.parent_id,
            use_cpu=task.use_cpu,
        )
        result = await super().invoke(enriched)
        # Fallback: when the LLM call errors (timeout, JSON parse fail,
        # template-leak placeholder output), the synthesizer would
        # otherwise admit ignorance — but we DO have real candidates
        # from the embedding search. Synthesise a VaultPlan from the
        # top hits ourselves so the user gets a useful answer.
        if result.error or not result.output or not result.output.get("hits"):
            log.info(
                "librarian fallback: LLM result %r — using top %d preflight hits",
                result.error or "empty hits", min(5, len(candidates)),
            )
            top = candidates[:5]
            fallback_hits = []
            for c in top:
                body = (c.get("body") or "").strip()
                if len(body) > 300:
                    body = body[:300] + "..."
                fallback_hits.append({
                    "path": c["path"],
                    "excerpt": body,
                })
            result.output = {
                "summary": (
                    f"Vault preflight returned {len(top)} hits for "
                    f"{query!r} (LLM ranking unavailable)"
                ),
                "hits": fallback_hits,
                "plan": ["preflight embed search", "fallback raw hits"],
            }
            result.error = None
            result.confidence = "medium"
        return result

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _extract_query(task: HelperTask) -> str:
        """Find the user's question in the task's inputs / goal."""
        for key in ("query", "topic", "question", "subject"):
            v = task.inputs.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        # Fall back to the goal itself.
        return (task.goal or "").strip()

    @staticmethod
    def _extract_audience(task: HelperTask) -> str:
        v = task.inputs.get("audience")
        if isinstance(v, str) and v.strip():
            return v.strip()
        # Fallback to the bot serving the turn — coordinator passes
        # `bot` through task.inputs so we can scope vault search to
        # the right audience without hardcoding "terry". Most vault
        # notes use `audience: [terry, claude-code]` (not "all"), so
        # the audience filter excludes them otherwise.
        bot = task.inputs.get("bot")
        if isinstance(bot, str) and bot.strip():
            return bot.strip()
        return "terry"

    async def _embed(self, query: str) -> list[float]:
        from shared.embeddings import embed_text
        vec = await embed_text(
            query, ollama_url=self._ollama_url, model=_EMBED_MODEL,
        )
        return vec or []

    async def _search_vault(
        self, query: str, audience: str,
    ) -> list[dict]:
        vec = await self._embed(query)
        if not vec:
            return []
        client = self._vault_client_factory()
        results = client.search(
            query_embedding=vec, k=_TOP_K, audience=audience,
            query_text=query,
        )
        out: list[dict] = []
        for r in results:
            body = (r.body or "").strip()
            if len(body) > _MAX_BODY_CHARS:
                body = body[:_MAX_BODY_CHARS] + "..."
            out.append({
                "path": r.path,
                "body": body,
                "score": round(float(r.score), 4),
            })
        return out
