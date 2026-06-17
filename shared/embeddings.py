"""Shared embedding helper with a small in-process cache.

Embeddings are deterministic for `(model, text)` and `nomic-embed-text`
runs CPU-pinned (~100-300 ms per call), so duplicate work across
helpers — librarian preflight, contradiction_detector, image_research,
relevance_gate, the /vault search route — adds up fast. A single
process-wide cache deduplicates them.

Cache shape: bounded LRU keyed by `(model, sha256(prefixed_text[:2000]))`.
The nomic task prefix is included in the key so document and query
embeddings never collide in cache even for identical text.

Failures are not cached — a transient Ollama drop shouldn't poison
later lookups.

Counters (`cache_hits`, `cache_misses`, `cache_errors`) are exposed via
`embedding_cache_stats()` so callers / health endpoints can observe
hit rate without reaching into internals.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from typing import Final

import httpx


log = logging.getLogger("shared.embeddings")

_MAX_PROMPT_CHARS: Final[int] = 2000
_DEFAULT_TIMEOUT_S: Final[float] = 15.0
_CACHE_MAX_ENTRIES: Final[int] = 4096

# (model, sha256_of_prompt_prefix) -> vector
_cache: "OrderedDict[tuple[str, str], list[float]]" = OrderedDict()
_cache_lock = asyncio.Lock()
_stats = {"hits": 0, "misses": 0, "errors": 0}

# nomic-embed-text task prefixes. Documents indexed with search_document:
# and queries issued with search_query: activate the asymmetric alignment
# the model was trained with, improving retrieval quality especially for
# short / acronym queries (e.g. "UEE") that sit far from their matching
# passages in raw vector space.
_NOMIC_PREFIX_DOCUMENT: Final[str] = "search_document: "
_NOMIC_PREFIX_QUERY: Final[str] = "search_query: "


def _nomic_prefix(kind: str) -> str:
    """Return the nomic task prefix for `kind` ("document" or "query")."""
    return _NOMIC_PREFIX_QUERY if kind == "query" else _NOMIC_PREFIX_DOCUMENT


def _key(model: str, text: str) -> tuple[str, str]:
    prefix = text[:_MAX_PROMPT_CHARS]
    return model, hashlib.sha256(prefix.encode("utf-8")).hexdigest()


async def embed_text(
    text: str,
    *,
    ollama_url: str,
    model: str,
    timeout: float = _DEFAULT_TIMEOUT_S,
    kind: str = "document",
) -> list[float] | None:
    """Return an embedding vector for `text`, or None on failure.

    `kind` controls the nomic task prefix prepended before sending:
      - "document" (default) → "search_document: <text>"  (indexing)
      - "query"              → "search_query: <text>"     (search-time)

    The prefix is included in the cache key so document and query
    embeddings for identical text are stored separately.

    Cached by `(model, sha256(prefixed_text[:2000]))`. Failures (Ollama
    down, non-200, malformed payload) return None and are NOT cached so
    a retry can succeed.
    """
    if not text:
        return None
    prefix = _nomic_prefix(kind)
    prefixed = prefix + text
    key = _key(model, prefixed)
    async with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            _stats["hits"] += 1
            return list(hit)
        _stats["misses"] += 1

    try:
        async with httpx.AsyncClient(
            base_url=ollama_url, timeout=timeout,
        ) as client:
            r = await client.post(
                "/api/embeddings",
                # num_gpu=0 pins nomic-embed to CPU so it never
                # competes with planner-qwen for GPU VRAM. See
                # vault_writer/embedder.py for the full rationale.
                json={
                    "model": model,
                    "prompt": prefixed[:_MAX_PROMPT_CHARS],
                    "options": {"num_gpu": 0},
                },
            )
            if r.status_code != 200:
                _stats["errors"] += 1
                return None
            vec = r.json().get("embedding")
            if not isinstance(vec, list) or not vec:
                _stats["errors"] += 1
                return None
            vector = [float(x) for x in vec]
    except Exception as e:  # noqa: BLE001
        _stats["errors"] += 1
        log.info("embed call failed (%s %s): %s", model, ollama_url, e)
        return None

    async with _cache_lock:
        _cache[key] = vector
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)

    return list(vector)


def embedding_cache_stats() -> dict[str, int]:
    """Snapshot of the cache counters + current size. Read-only."""
    return {
        "hits": _stats["hits"],
        "misses": _stats["misses"],
        "errors": _stats["errors"],
        "size": len(_cache),
    }


def reset_embedding_cache() -> None:
    """Test-only: drop everything (cache + counters). Production code
    should never need this — the cache is process-lifetime."""
    _cache.clear()
    _stats["hits"] = 0
    _stats["misses"] = 0
    _stats["errors"] = 0
