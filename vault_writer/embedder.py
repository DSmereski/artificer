"""Async Ollama embeddings client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx


class EmbeddingError(RuntimeError):
    """Raised when an embedding request fails or returns unexpected shape."""


# nomic-embed-text on Ollama has a hard ceiling around 2048 tokens — beyond
# that, the request 500s with "the input length exceeds the context length".
# ~4 chars per token for English text; we target ≤1000 tokens (~4000 chars)
# per chunk, well below the 2048-token ceiling, so even token-dense content
# (code, JSON, lists with few spaces) stays safe.
_CHUNK_SIZE = 4000       # characters per chunk
_CHUNK_OVERLAP = 400     # character overlap between consecutive chunks

# nomic-embed-text is trained with asymmetric task prefixes:
#   search_document: <text>  — for passages being indexed
#   search_query:   <text>  — for query-time lookups
# Without these prefixes nomic treats both as untyped text and loses the
# asymmetric alignment that improves retrieval quality. Adding them here
# (one call-site in embedder) rather than in every caller keeps the
# prefix logic centralised and easy to update if the model changes.
_PREFIX_DOCUMENT = "search_document: "
_PREFIX_QUERY = "search_query: "


def chunk_text(text: str, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Split `text` into overlapping chunks of at most `chunk_size` characters.

    Tries to split on paragraph boundaries (double newline) within the last
    20 % of the chunk window so chunks end at natural sentence/section breaks.
    Falls back to a hard character split when no paragraph boundary exists
    (e.g. a single massive line — the pathological case that caused the
    imagegen-loras.md 500s).

    Returns at least one chunk even for empty input so callers don't need a
    special-case for empty text.
    """
    if not text:
        return [""]
    if len(text) <= chunk_size:
        return [text]

    # Overlap must be strictly less than chunk_size; clamp defensively so a
    # small custom chunk_size (e.g. in tests) does not cause advance=1 loops.
    effective_overlap = min(overlap, max(chunk_size // 4, 0))
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            # Look for a paragraph break in the last 20 % of the window so we
            # split at a natural boundary rather than mid-sentence.
            search_from = start + int(chunk_size * 0.80)
            boundary = text.rfind("\n\n", search_from, end)
            if boundary != -1:
                end = boundary + 2  # include the blank line in this chunk
        chunk = text[start:end]
        if chunk:
            chunks.append(chunk)
        # When we have consumed to the end of the text we are done.  Without
        # this break the overlap arithmetic would produce (end - start - overlap)
        # <= 0 for short tail chunks, causing advance=1 and hundreds of
        # single-character splinters at the tail of the text.
        if end >= len(text):
            break
        # Advance by (chunk_size - effective_overlap), always making progress.
        advance = max(end - start - effective_overlap, 1)
        start += advance
    return chunks if chunks else [""]


@dataclass(frozen=True, slots=True)
class Embedder:
    client: httpx.AsyncClient
    model: str
    dimension: int

    async def embed(
        self,
        text: str,
        *,
        kind: Literal["document", "query"] = "document",
    ) -> list[float]:
        """Embed `text` via Ollama's /api/embeddings endpoint.

        Prepends the appropriate nomic task prefix before sending:
          - kind="document"  →  "search_document: <text>"  (indexing)
          - kind="query"     →  "search_query: <text>"     (search-time)

        Delegates to `embed_chunks` (which handles chunking) and returns
        the embedding for the first chunk, which contains the document's
        title and lead content — the most semantically distinctive region
        for vault notes. The full note body is stored in the `notes` table
        and indexed by FTS5 regardless of length, so later chunks are never
        silently lost from search.
        """
        chunks = chunk_text(text)
        return await self._embed_one(chunks[0], kind=kind)

    async def embed_chunks(
        self,
        text: str,
        *,
        kind: Literal["document", "query"] = "document",
        chunk_size: int | None = None,
    ) -> list[list[float]]:
        """Embed all chunks of `text` and return one embedding per chunk.

        Prepends the nomic task prefix on every chunk. Callers that want
        to index every chunk separately (e.g. per-chunk vector search)
        should use this method. The returned list has the same length as
        ``chunk_text(text, chunk_size or _CHUNK_SIZE)``.

        `chunk_size` overrides the default ``_CHUNK_SIZE`` so operators
        can tune the character budget via config (e.g. lower it for
        token-dense content like code or JSON that pushes nomic-embed-text
        past its ~2048-token context ceiling).
        """
        effective_size = chunk_size if chunk_size is not None else _CHUNK_SIZE
        chunks = chunk_text(text, chunk_size=effective_size)
        results: list[list[float]] = []
        for chunk in chunks:
            results.append(await self._embed_one(chunk, kind=kind))
        return results

    async def _embed_one(
        self,
        text: str,
        *,
        kind: Literal["document", "query"] = "document",
    ) -> list[float]:
        """Send a single embedding request for `text`.

        Prepends the nomic task prefix appropriate for `kind` before
        sending. `text` must already be within the model's token budget
        — callers are responsible for splitting before calling this
        method. Raises `EmbeddingError` on HTTP errors or unexpected
        response shapes.
        """
        prefix = _PREFIX_QUERY if kind == "query" else _PREFIX_DOCUMENT
        prefixed = prefix + text
        try:
            resp = await self.client.post(
                "/api/embeddings",
                # num_gpu=0 forces CPU execution. The embedder is a
                # 137M F16 model — warm CPU latency is ~0.22s per
                # call, marginally faster than warm GPU (~0.29s) once
                # transfer overhead is included. Pinning to CPU
                # prevents Ollama from making an aggressive load-time
                # eviction of larger GPU-resident models (notably
                # planner-qwen at 9.5GB) when the embedder is invoked
                # mid-conversation. Observed scenario 10, 2026-05-02:
                # nomic-embed loading triggered planner-qwen ->
                # CPU-fallback, breaking every helper for the rest of
                # the run.
                json={
                    "model": self.model,
                    "prompt": prefixed,
                    "options": {"num_gpu": 0},
                },
                timeout=30.0,
            )
        except httpx.HTTPError as e:
            raise EmbeddingError(f"ollama request failed: {e}") from e
        if resp.status_code != 200:
            raise EmbeddingError(
                f"ollama returned {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json()
        vec = data.get("embedding")
        if not isinstance(vec, list) or not all(isinstance(x, (int, float)) for x in vec):
            raise EmbeddingError(f"ollama response missing embedding: {data}")
        if len(vec) != self.dimension:
            raise EmbeddingError(
                f"embedding dimension {len(vec)} != expected {self.dimension}"
            )
        return [float(x) for x in vec]
