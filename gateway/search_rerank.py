"""LLM re-rank step for user-initiated hybrid search.

After RRF fuses the vector + FTS5 results, this module sends the top-N
candidates to a cheap LLM (via OllamaInvoker) and asks it to re-order
them by semantic relevance to the user's query. The result is a re-sorted
list of the same candidate dicts — the dict shape is unchanged.

Design decisions:
- Failure-tolerant: any error (network, timeout, bad JSON) returns the
  original RRF order unchanged. Never raises; never breaks search.
- Candidates capped at 20 before the LLM call to keep prompts small.
- Each snippet truncated to 400 chars for the same reason.
- Uses OllamaInvoker directly (same as helpers/base.py) — no new dep.
- The LLM callable is injected at call-time, not at import time, so
  tests can swap it without monkey-patching module state.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger("gateway.search_rerank")

_MAX_CANDIDATES = 20
_SNIPPET_CHARS = 400

# The prompt is loaded lazily (once) from prompts/search_rerank.md.
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "search_rerank.md"
_cached_prompt: str | None = None


def _load_prompt() -> str:
    global _cached_prompt
    if _cached_prompt is None:
        _cached_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    return _cached_prompt


def _snippet(candidate: dict[str, Any]) -> str:
    """Extract a short text snippet from a candidate dict.

    Works for both note dicts (key: ``body``) and chat-log dicts
    (key: ``content``). Falls back to an empty string.
    """
    text = candidate.get("body") or candidate.get("content") or ""
    text = str(text).strip().replace("\n", " ")
    return text[:_SNIPPET_CHARS]


def _build_prompt_user(query: str, candidates: list[dict[str, Any]]) -> str:
    """Format the numbered candidate list for the LLM."""
    lines = [f'Query: "{query}"', "", "Candidates:"]
    for idx, c in enumerate(candidates):
        lines.append(f"{idx}. {_snippet(c)}")
    return "\n".join(lines)


def _parse_index_array(text: str, n: int) -> list[int] | None:
    """Extract a JSON array of integers from the LLM reply.

    Returns None when the reply is unparseable or contains out-of-range
    indices (so the caller can fall back to the original order).
    """
    if not text or not text.strip():
        return None
    # Strip Qwen3-style reasoning blocks.
    import re
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Find the first JSON array in the reply.
    m = re.search(r"\[[\s\d,]+\]", text, re.DOTALL)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(arr, list):
        return None
    # Validate: all elements must be integers in [0, n).
    out: list[int] = []
    seen: set[int] = set()
    for item in arr:
        if not isinstance(item, int):
            return None
        if item < 0 or item >= n:
            return None
        if item in seen:
            continue  # drop duplicates gracefully
        seen.add(item)
        out.append(item)
    if not out:
        return None
    return out


async def llm_rerank(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    limit: int = 20,
    llm_chat: Callable[..., Awaitable[tuple[str, int, int]]] | None = None,
    ollama_name: str = "llama3.2",
) -> list[dict[str, Any]]:
    """Re-order `candidates` by semantic relevance to `query` using an LLM.

    Parameters
    ----------
    query:
        The user's search query.
    candidates:
        RRF-ranked list of result dicts (note or chat-log shape).
    limit:
        Maximum candidates to return. Applied *after* re-ranking.
    llm_chat:
        Async callable with the same signature as
        ``OllamaInvoker.chat(*, model, system, user, params, use_cpu)``.
        When None, ``OllamaInvoker().chat`` is used.
    ollama_name:
        Model name passed to the invoker. Callers can override to pick
        the cheapest available model.

    Returns
    -------
    list[dict]
        The same dict shape as `candidates`, re-ordered by LLM score.
        Truncated to `limit`. On any error, returns the original
        ``candidates[:limit]`` unchanged.
    """
    if not candidates:
        return []

    # Cap the slice sent to the LLM.
    to_rank = candidates[:_MAX_CANDIDATES]

    if llm_chat is None:
        from gateway.helpers.base import OllamaInvoker
        llm_chat = OllamaInvoker().chat

    try:
        system = _load_prompt()
    except OSError as e:
        log.warning("search_rerank: could not load prompt: %s", e)
        return candidates[:limit]

    user_msg = _build_prompt_user(query, to_rank)

    try:
        text, _ti, _to = await llm_chat(
            model=ollama_name,
            system=system,
            user=user_msg,
            params={"temperature": 0},
            use_cpu=False,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("search_rerank: LLM call failed: %s", e)
        return candidates[:limit]

    indices = _parse_index_array(text, len(to_rank))
    if indices is None:
        log.warning(
            "search_rerank: unparseable LLM reply (returning original order): %r",
            text[:200],
        )
        return candidates[:limit]

    # Build the re-ordered list. Any candidate not mentioned by the LLM
    # is appended at the end in original order so nothing is lost.
    mentioned = set(indices)
    tail = [c for i, c in enumerate(to_rank) if i not in mentioned]
    reranked = [to_rank[i] for i in indices] + tail
    # Append any candidates beyond _MAX_CANDIDATES (untouched by LLM).
    reranked += candidates[_MAX_CANDIDATES:]
    return reranked[:limit]
