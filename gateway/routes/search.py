"""Unified search across vault notes, chat history, entity pages,
recent images, and the escalation queue.

`GET /v1/search?q=<term>&kinds=<csv>&limit=<n>` fans out to whichever
surfaces are listed in `kinds` (default: all) and returns a single
ranked list of `SearchHit` records. Each hit carries a `kind`
discriminator so the UI can render the right tile.

Audience clamp: vault-note results respect the device's audience
(via `VaultClient.search_notes_fts`). Chat / entity / image /
escalation surfaces are inherently single-owner — they have no
audience field — so no extra clamp is applied there.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from gateway.deps import require_device, state
from gateway.routes.chat import _stable_user_id


router = APIRouter(prefix="/v1/search", tags=["search"])
log = logging.getLogger("gateway.search")


def _vault_client(st):
    """Return the lifespan-managed singleton if present, else fall back
    to a fresh client. Mirrors `gateway.routes.vault._vault_client`."""
    existing = getattr(st, "vault_client", None)
    if existing is not None:
        return existing
    from shared.vault_client import VaultClient
    return VaultClient(
        vault_path=st.config.vault_path,
        daemon_host=st.config.vault_writer.host,
        daemon_port=st.config.vault_writer.port,
    )

_VALID_KINDS = ("vault", "chat", "entity", "image", "escalation")


class SearchHit(BaseModel):
    kind: str                              # vault | chat | entity | image | escalation
    title: str
    preview: str
    score: float                           # higher = better; comparable across kinds
    ts: int                                # unix epoch seconds (for tiebreak / display)
    ref: dict[str, Any] = Field(default_factory=dict)


def _parse_kinds(kinds: str | None) -> list[str]:
    if not kinds:
        return list(_VALID_KINDS)
    parts = [p.strip().lower() for p in kinds.split(",") if p.strip()]
    return [p for p in parts if p in _VALID_KINDS] or list(_VALID_KINDS)


def _truncate(text: str, n: int = 240) -> str:
    s = (text or "").strip().replace("\n", " ")
    return (s[:n] + "...") if len(s) > n else s


def _rrf_score(rank: int) -> float:
    """1/(60 + rank) — Reciprocal Rank Fusion. Same K used inside
    vault_writer.index, so scores from this route are comparable to
    the hybrid notes search."""
    return 1.0 / (60 + max(1, rank))


@router.get("", response_model=list[SearchHit])
def unified_search(
    q: str = Query(..., min_length=1, max_length=500),
    kinds: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    device=Depends(require_device),
    request: Request = None,
) -> list[SearchHit]:
    st = state(request)
    selected = _parse_kinds(kinds)
    audience = (device.audience[0] if device.audience else "all")
    user_id = _stable_user_id(device.user)
    qlower = q.lower()
    hits: list[SearchHit] = []

    # --- Vault notes ----------------------------------------------------
    if "vault" in selected:
        try:
            vc = _vault_client(st)
            rows = vc.search_notes_fts(
                query_text=q, audience=audience, limit=limit,
            )
            for rank, r in enumerate(rows, start=1):
                fm = r.get("frontmatter") or {}
                title = (
                    fm.get("title") if isinstance(fm.get("title"), str) else None
                ) or r.get("path", "vault note")
                hits.append(SearchHit(
                    kind="vault",
                    title=str(title),
                    preview=_truncate(r.get("body", "")),
                    score=_rrf_score(rank),
                    ts=0,
                    ref={"path": r.get("path", "")},
                ))
        except Exception as e:  # noqa: BLE001
            log.warning("unified_search: vault leg failed: %s", e)

    # --- Chat log -------------------------------------------------------
    if "chat" in selected:
        try:
            vc = _vault_client(st)
            rows = vc.search_chat(
                bot="hive", user_id=user_id,
                query_text=q, limit=limit,
            )
            for rank, r in enumerate(rows, start=1):
                hits.append(SearchHit(
                    kind="chat",
                    title=f"{r.get('role', '?')} — turn {r.get('turn_id') or '?'}",
                    preview=_truncate(r.get("content", "")),
                    score=_rrf_score(rank),
                    ts=int(r.get("created_at") or 0),
                    ref={
                        "thread_id": r.get("thread_id", "default"),
                        "turn_id": r.get("turn_id"),
                        "role": r.get("role"),
                    },
                ))
        except Exception as e:  # noqa: BLE001
            log.warning("unified_search: chat leg failed: %s", e)

    # --- Entity pages ---------------------------------------------------
    if "entity" in selected:
        try:
            vc = _vault_client(st)
            rows = vc.search_entity_pages(query_text=q, limit=limit)
            for rank, r in enumerate(rows, start=1):
                hits.append(SearchHit(
                    kind="entity",
                    title=str(r.get("title") or r.get("id") or "entity"),
                    preview=_truncate(r.get("compiled_truth", "")),
                    score=_rrf_score(rank),
                    ts=int(r.get("last_mentioned_at") or 0),
                    ref={
                        "slug": r.get("id", ""),
                        "kind": r.get("kind", "concept"),
                    },
                ))
        except Exception as e:  # noqa: BLE001
            log.warning("unified_search: entity leg failed: %s", e)

    # --- Recent images (LIKE on prompt) --------------------------------
    if "image" in selected:
        store = st.recent_images
        if store is not None:
            try:
                jobs = store.all_recent(limit=200)
                matched: list[Any] = [
                    j for j in jobs
                    if qlower in (j.prompt or "").lower()
                ]
                for rank, j in enumerate(matched[:limit], start=1):
                    hits.append(SearchHit(
                        kind="image",
                        title=_truncate(j.prompt or "(no prompt)", 80),
                        preview=f"{j.state} · job {j.job_id}",
                        score=_rrf_score(rank),
                        ts=int(j.created_at or 0),
                        ref={
                            "job_id": j.job_id,
                            "result_ids": list(j.result_ids or []),
                            "bot": j.bot,
                        },
                    ))
            except Exception as e:  # noqa: BLE001
                log.warning("unified_search: image leg failed: %s", e)

    # --- Escalations ----------------------------------------------------
    if "escalation" in selected:
        store = st.escalation_store
        if store is not None:
            try:
                escs = store.list(include_resolved=False)
                matched_e = [
                    e for e in escs
                    if qlower in (e.title or "").lower()
                    or qlower in (e.summary or "").lower()
                    or qlower in (e.user_msg or "").lower()
                ]
                for rank, e in enumerate(matched_e[:limit], start=1):
                    hits.append(SearchHit(
                        kind="escalation",
                        title=e.title or e.slug,
                        preview=_truncate(e.summary or e.user_msg or ""),
                        score=_rrf_score(rank),
                        ts=0,
                        ref={
                            "slug": e.slug,
                            "severity": e.severity,
                            "device_id": e.device_id,
                        },
                    ))
            except Exception as e:  # noqa: BLE001
                log.warning("unified_search: escalation leg failed: %s", e)

    hits.sort(key=lambda h: (h.score, h.ts), reverse=True)
    return hits[:limit]
