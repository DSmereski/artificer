"""GET /v1/wiki/reviews — open wiki review items (contradictions + gaps).

The vault_writer wiki_synth ANALYZE step finds contradictions and gaps in
source notes.  Those are queued in the ``wiki_reviews`` SQLite table
(same DB as the vault index).  This route exposes them so the dashboard
can poll for a "needs review" badge/rail.

POST /v1/wiki/reviews/{id}/resolve  — mark an item resolved or dismissed.
POST /v1/wiki/reviews/{id}/research — trigger gap-fill for a gap item.
GET  /v1/wiki/reviews/count         — lightweight badge count.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from gateway.deps import require_device, require_device_or_loopback, state

router = APIRouter(prefix="/v1/wiki", tags=["wiki"])
log = logging.getLogger("gateway.wiki")


def _review_conn(request: Request) -> sqlite3.Connection:
    """Open a connection to the vault DB that holds wiki_reviews."""
    st = state(request)
    vault_path = st.config.vault_path
    db_path = vault_path / ".vault-writer" / "vault.db"
    if not db_path.exists():
        raise HTTPException(503, "vault DB not found")
    return sqlite3.connect(str(db_path), timeout=5)


@router.get("/reviews")
async def get_reviews(
    request: Request,
    limit: int = 50,
    device=Depends(require_device_or_loopback),
) -> JSONResponse:
    """Return open wiki review items, newest first."""
    from vault_writer.review_queue import ensure_schema, get_open_reviews

    conn = _review_conn(request)
    try:
        ensure_schema(conn)
        items = get_open_reviews(conn, limit=min(limit, 200))
    finally:
        conn.close()
    return JSONResponse({"reviews": items, "count": len(items)})


@router.get("/reviews/count")
async def reviews_count(
    request: Request,
    device=Depends(require_device_or_loopback),
) -> JSONResponse:
    """Lightweight badge count of open review items."""
    from vault_writer.review_queue import ensure_schema, count_open

    conn = _review_conn(request)
    try:
        ensure_schema(conn)
        n = count_open(conn)
    finally:
        conn.close()
    return JSONResponse({"count": n})


@router.post("/reviews/{review_id}/resolve")
async def resolve(
    request: Request,
    review_id: int,
    status: str = "resolved",
    device=Depends(require_device),
) -> JSONResponse:
    """Mark a review item as resolved or dismissed."""
    from vault_writer.review_queue import ensure_schema, resolve_review

    if status not in ("resolved", "dismissed"):
        raise HTTPException(400, f"invalid status {status!r}; use 'resolved' or 'dismissed'")
    conn = _review_conn(request)
    try:
        ensure_schema(conn)
        ok = resolve_review(conn, review_id, status=status)
    finally:
        conn.close()
    if not ok:
        raise HTTPException(404, f"review {review_id} not found or already resolved")
    return JSONResponse({"ok": True, "id": review_id, "status": status})


@router.post("/reviews/{review_id}/research")
async def research(
    request: Request,
    review_id: int,
    confirm: bool = False,
    device=Depends(require_device),
) -> JSONResponse:
    """Trigger gap-fill research for a gap/contradiction review item.

    With confirm=False (default), returns proposed search topics and
    preview results without ingesting anything.
    With confirm=True, ingests results through the vault learn path.

    Safety: web results are UNTRUSTED — gap_fill fences them as DATA
    before passing to the vault pipeline (prompt-injection-defense).
    Ingest requires confirm=True to prevent accidental auto-ingest.
    """
    from vault_writer.review_queue import ensure_schema, get_open_reviews

    conn = _review_conn(request)
    try:
        ensure_schema(conn)
        items = get_open_reviews(conn, limit=200)
    finally:
        conn.close()

    item = next((i for i in items if i["id"] == review_id), None)
    if item is None:
        raise HTTPException(404, f"review {review_id} not found or not open")

    gap_description: str = item.get("summary", "")
    if not gap_description:
        raise HTTPException(400, "review item has no summary text to research")

    # Build the LLM function from gateway config.
    ai_team: Any = getattr(request.app.state, "ai_team", None)
    llm_fn = _build_llm_fn(ai_team)
    if llm_fn is None:
        raise HTTPException(503, "no LLM backend configured for gap-fill")

    # Build learn_fn when confirm=True.
    learn_fn = None
    if confirm:
        learn_fn = _build_learn_fn(request)

    from vault_writer.gap_fill import gap_fill

    result = gap_fill(
        gap_description,
        llm_fn=llm_fn,
        confirm=confirm,
        learn_fn=learn_fn,
    )

    return JSONResponse({
        "ok": result.ok,
        "review_id": review_id,
        "topics": result.topics,
        "results_count": len(result.search_results),
        "results_preview": result.search_results[:5],
        "ingested": result.ingested,
        "skipped_reason": result.skipped_reason,
        "error": result.error,
        "confirm_required": not confirm and result.ok,
    })


# ---------------------------------------------------------------------------
# Helper factories (keep routes thin)
# ---------------------------------------------------------------------------


def _build_llm_fn(ai_team: Any):
    """Try to build a synchronous llm_fn from the gateway's Ollama config."""
    try:
        config = getattr(ai_team, "config", None)
        wiki_cfg = getattr(config, "wiki_synth", None)
        if wiki_cfg is None:
            return None
        ollama_url = getattr(wiki_cfg, "ollama_url", None) or "http://127.0.0.1:11434"
        model = getattr(wiki_cfg, "model", None) or "qwen3:7b"
        from vault_writer.wiki_synth import make_ollama_llm_fn
        return make_ollama_llm_fn(ollama_url, model)
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki/research: could not build llm_fn: %s", exc)
        return None


def _build_learn_fn(request: Request):
    """Return a learn_fn that routes through the vault-writer learn path."""
    from shared.vault_client import VaultClient

    ai_team = getattr(request.app.state, "ai_team", None)
    config = getattr(ai_team, "config", None)
    vault_cfg = getattr(config, "vault_writer", None)
    if vault_cfg is None:
        return None

    vault_client = VaultClient(
        host=vault_cfg.host,
        port=vault_cfg.port,
        token_path=getattr(vault_cfg, "token_path", None),
    )

    import asyncio

    def learn_fn(title: str, body: str, source_url: str) -> None:
        """Synchronous wrapper that fires-and-forgets the async vault learn call."""
        async def _call():
            await vault_client.learn(
                category="research",
                title=title,
                body=body,
                author="gap-fill",
                audience=["all"],
            )

        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(_call())
        except Exception as exc:  # noqa: BLE001
            log.warning("wiki/research: learn_fn failed for %r: %s", title, exc)

    return learn_fn
