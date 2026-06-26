"""Vault routes: search, tree, note read, learn (write).

All proxies over existing shared.vault_client + the running vault-writer daemon.
Audience scoping is honoured: each device has an `audience` tag (default
["all"]) and reads are filtered server-side.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from gateway.deps import rate_limited, require_device, state


router = APIRouter(prefix="/v1/vault", tags=["vault"])
log = logging.getLogger("gateway.vault")


def _vault_client(st):
    """Return the lifespan-managed singleton if present, else fall back
    to a fresh client. The singleton avoids opening a new daemon socket
    per request once production wiring is in place; the fallback keeps
    test configs (which build AppState without the lifespan) working.
    """
    existing = getattr(st, "vault_client", None)
    if existing is not None:
        return existing
    from shared.vault_client import VaultClient
    return VaultClient(
        vault_path=st.config.vault_path,
        daemon_host=st.config.vault_writer.host,
        daemon_port=st.config.vault_writer.port,
    )


# --- Search --------------------------------------------------------------------

class SearchHit(BaseModel):
    path: str
    type: str
    author: str
    audience: list[str]
    score: float
    preview: str
    # Frontmatter title when present, else None. The app prefers this
    # over the raw path slug when rendering hit tiles — paths like
    # `knowledge/2026/04/kraken-star-citizen-spaceship.md` are ugly
    # while `Kraken Star Citizen Spaceship` is human-readable.
    title: str | None = None


async def _embed_query(ollama_url: str, model: str, text: str) -> list[float]:
    from shared.embeddings import embed_text
    # Use kind="query" so the nomic search_query: prefix is applied.
    # Documents are indexed with search_document: (daemon); queries use
    # search_query: here so the asymmetric alignment the model was
    # trained with is active at retrieval time.
    vec = await embed_text(
        text, ollama_url=ollama_url, model=model, timeout=30.0, kind="query",
    )
    if not vec:
        raise HTTPException(status_code=502, detail="bad embedding response")
    return vec


def _estimate_tokens(text: str) -> int:
    """Fast token estimate: chars / 4 (GPT-family rule of thumb)."""
    return max(1, len(text) // 4)


def _apply_token_budget(
    hits: list["SearchHit"],
    bodies: list[str],
    budget: int,
) -> list["SearchHit"]:
    """Allocate at most `budget` tokens across `hits` (ranked by score desc).

    Algorithm:
      1. Always keep the top hit (index 0) whole — never truncate it.
      2. Walk remaining hits in score order. For each, check if its full
         body fits in the remaining budget. If yes, keep it whole. If no,
         truncate its body proportionally to whatever is left, then stop.
         Hits whose truncated share would be zero are dropped.

    `bodies` is the parallel raw body list (before preview truncation) used
    for accurate token counting. The returned hits have already had their
    `preview` field adjusted to the truncated body where applicable.
    """
    if not hits or budget <= 0:
        return hits

    out: list["SearchHit"] = []
    remaining = budget

    for i, (hit, body) in enumerate(zip(hits, bodies)):
        toks = _estimate_tokens(body)
        if i == 0:
            # Top hit is always kept whole regardless of budget.
            out.append(hit)
            remaining -= toks
            continue
        if remaining <= 0:
            break
        if toks <= remaining:
            out.append(hit)
            remaining -= toks
        else:
            # Truncate body to remaining token budget.
            chars_allowed = remaining * 4
            if chars_allowed < 1:
                break
            truncated = body[:chars_allowed].rstrip() + "..."
            preview = truncated.replace("\n", " ")
            out.append(hit.model_copy(update={"preview": preview}))
            remaining = 0
            break

    return out


@router.get("/search", response_model=list[SearchHit])
async def vault_search(
    q: str = Query(..., min_length=1, max_length=500),
    k: int = Query(default=5, ge=1, le=50),
    expand_links: bool = Query(default=False),
    max_hops: int = Query(default=1, ge=0, le=3),
    budget: int = Query(
        default=0, ge=0,
        description=(
            "Token budget for returned bodies (chars/4 estimate). "
            "0 = unlimited. Top hit is always kept whole; "
            "lower-ranked hits are truncated or dropped to fit."
        ),
    ),
    device=Depends(require_device),
    request: Request = None,
) -> list[SearchHit]:
    st = state(request)
    audience = device.audience[0] if device.audience else "all"
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    embed_model = getattr(
        getattr(st.config, "vault_writer", None), "embed_model", "nomic-embed-text",
    ) or "nomic-embed-text"
    vec = await _embed_query(ollama_host, embed_model, q)

    client = _vault_client(st)
    # Hybrid: vector + FTS5 BM25 fused via RRF inside the index. The
    # raw query text is forwarded so the index's keyword half can run
    # alongside the vector kNN. The route doesn't need to merge or
    # rerank — the index returns a single fused list.
    results = client.search(
        query_embedding=vec, k=k, audience=audience, query_text=q,
    )
    hits: list[SearchHit] = []
    bodies: list[str] = []  # parallel raw bodies for token budget accounting
    seen_paths: set[str] = set()
    for r in results:
        body = r.body.strip()
        preview = body.replace("\n", " ")
        if len(preview) > 400:
            preview = preview[:400] + "..."
        hits.append(SearchHit(
            path=r.path, type=r.note_type, author=r.author,
            audience=list(r.audience), score=round(r.score, 4),
            preview=preview,
            title=_title_for(st.config.vault_path, r.path),
        ))
        bodies.append(body)
        seen_paths.add(r.path)

    # Graph-walking RAG: for every seed hit, pull in the notes it [[links]] to.
    # These arrive with score=0 to mark them as linked-context, not semantic
    # matches. Callers that want the vanilla ranking can set expand_links=false.
    if expand_links and results:
        seeds = [(r.path, r.body) for r in results]
        expanded = client.expand_with_wikilinks(
            seeds, audience=audience, max_hops=max_hops, max_notes=k * 3,
        )
        for path, body in expanded:
            if path in seen_paths:
                continue
            body_stripped = body.strip()
            preview = body_stripped.replace("\n", " ")
            if len(preview) > 400:
                preview = preview[:400] + "..."
            hits.append(SearchHit(
                path=path, type="linked", author="vault",
                audience=["all"], score=0.0, preview=preview,
                title=_title_for(st.config.vault_path, path),
            ))
            bodies.append(body_stripped)
            seen_paths.add(path)

    if budget > 0:
        hits = _apply_token_budget(hits, bodies, budget)

    return hits


def _title_for(vault_path: Path, rel_path: str) -> str | None:
    """Best-effort title resolution. Frontmatter `title` if present;
    otherwise a title-cased slug of the filename. Returns None only
    when the note is missing on disk."""
    try:
        full = (vault_path / rel_path).resolve()
        full.relative_to(vault_path.resolve())  # confine
    except (OSError, ValueError):
        return None
    if not full.is_file():
        return None
    try:
        raw = full.read_text(encoding="utf-8", errors="replace")
        from vault_writer.util import parse_frontmatter
        fm, _ = parse_frontmatter(raw)
        title = fm.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    except Exception:
        pass
    # Slug fallback. 'kraken-star-citizen-ship' → 'Kraken Star Citizen Ship'.
    parts = [p for p in full.stem.split("-") if p]
    return " ".join(p[:1].upper() + p[1:] for p in parts) if parts else None


# --- Tree ----------------------------------------------------------------------

class TreeNode(BaseModel):
    name: str
    path: str                 # vault-relative
    is_dir: bool
    children: list["TreeNode"] = Field(default_factory=list)


TreeNode.model_rebuild()


def _build_tree(root: Path) -> TreeNode:
    def _walk(p: Path) -> TreeNode:
        rel = p.relative_to(root).as_posix() if p != root else ""
        if p.is_dir():
            kids: list[TreeNode] = []
            try:
                entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
            except OSError:
                entries = []
            for e in entries:
                if e.name.startswith("."):
                    continue
                if e.is_file() and not e.name.endswith(".md"):
                    continue
                kids.append(_walk(e))
            return TreeNode(name=p.name or rel or "vault", path=rel, is_dir=True, children=kids)
        return TreeNode(name=p.name, path=rel, is_dir=False, children=[])
    return _walk(root)


@router.get("/tree", response_model=TreeNode)
def vault_tree(device=Depends(require_device), request: Request = None) -> TreeNode:
    st = state(request)
    return _build_tree(st.config.vault_path)


# --- Note read -----------------------------------------------------------------

class NoteContent(BaseModel):
    path: str
    body: str
    size_bytes: int


@router.get("/note", response_model=NoteContent)
def read_note(
    path: str = Query(..., min_length=1, max_length=512),
    device=Depends(require_device),
    request: Request = None,
) -> NoteContent:
    st = state(request)
    root = st.config.vault_path.resolve()
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="path escapes vault") from e
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    if not target.name.endswith(".md"):
        raise HTTPException(status_code=400, detail="only .md notes supported")
    try:
        body = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"read failed: {e}") from e
    return NoteContent(path=path, body=body, size_bytes=len(body.encode("utf-8")))


# --- Note delete ---------------------------------------------------------------


@router.delete("/note", status_code=204)
def delete_note(
    path: str = Query(..., min_length=1, max_length=512),
    device=Depends(rate_limited("writes")),
    request: Request = None,
) -> None:
    """Unlink a vault note by relative path.

    Audience-gated: a non-`all` device can only delete notes whose
    audience permits it. Path is confined to the vault root, .md only,
    no recursion (single file). The vault-writer daemon will pick up
    the deletion via its filesystem watcher and remove the embedding.
    """
    st = state(request)
    root = st.config.vault_path.resolve()
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="path escapes vault") from e
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    if not target.name.endswith(".md"):
        raise HTTPException(status_code=400, detail="only .md notes supported")
    if any(part.startswith(".") for part in target.relative_to(root).parts):
        raise HTTPException(status_code=400, detail="cannot delete dotfiles")
    # Audience check before unlink. **Fail-closed** — if frontmatter
    # is unreadable or malformed we refuse the delete rather than
    # default to `audience: [all]` and let any paired device drop it.
    # The previous fail-open default was a real privilege bug: a
    # malformed-YAML note got handed back as "all-audience" and any
    # device could wipe it.
    try:
        from vault_writer.util import audience_matches, parse_frontmatter
        raw = target.read_text(encoding="utf-8", errors="replace")
        fm, _ = parse_frontmatter(raw)
    except Exception as e:
        raise HTTPException(
            status_code=403,
            detail="cannot verify audience (unreadable frontmatter)",
        ) from e
    note_audience = list(fm.get("audience") or ["all"])
    caller_audiences = list(device.audience) if device.audience else ["all"]
    # Honour the FULL audience tuple, not just [0]. A device paired as
    # ('hive', 'claude-code') was previously checked only as 'hive'.
    if not any(audience_matches(a, note_audience) for a in caller_audiences):
        raise HTTPException(status_code=403, detail="audience denies delete")
    try:
        target.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"unlink failed: {e}") from e
    return None


# --- Stats ---------------------------------------------------------------------

class VaultStats(BaseModel):
    notes: int
    by_top_level: dict[str, int]
    total_size_bytes: int


@router.get("/stats", response_model=VaultStats)
def vault_stats(
    device=Depends(require_device),
    request: Request = None,
) -> VaultStats:
    """Lightweight stats for the Settings tab."""
    st = state(request)
    root = st.config.vault_path
    by_top: dict[str, int] = {}
    total = 0
    notes = 0
    if root.is_dir():
        for p in root.rglob("*.md"):
            if any(part.startswith(".") for part in p.parts):
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            notes += 1
            total += size
            try:
                rel_parts = p.relative_to(root).parts
            except ValueError:
                continue
            # Files at the vault root (README.md, etc.) get bucketed
            # under "_root" rather than reporting their own filename as
            # a folder name.
            top = rel_parts[0] if len(rel_parts) > 1 else "_root"
            by_top[top] = by_top.get(top, 0) + 1
    return VaultStats(notes=notes, by_top_level=by_top, total_size_bytes=total)


# --- Wikilink resolve ----------------------------------------------------------

class WikilinkHit(BaseModel):
    path: str
    body: str


@router.get("/wikilink", response_model=WikilinkHit)
def resolve_wikilink(
    name: str = Query(..., min_length=1, max_length=256),
    device=Depends(require_device),
    request: Request = None,
) -> WikilinkHit:
    """Resolve an Obsidian [[Note Name]] ref to (path, body).

    Used by the app's note viewer when the user taps a wikilink. Returns 404
    if the note can't be found or the device's audience doesn't cover it.
    """
    st = state(request)
    client = _vault_client(st)
    audience = device.audience[0] if device.audience else "all"
    resolved = client.resolve_wikilinks([name], audience=audience)
    if not resolved:
        raise HTTPException(status_code=404, detail="wikilink target not found")
    path, body = resolved[0]
    return WikilinkHit(path=path, body=body)


# --- Backlinks ----------------------------------------------------------------


class BacklinkHit(BaseModel):
    path: str
    title: str | None = None
    preview: str


@router.get("/backlinks", response_model=list[BacklinkHit])
def vault_backlinks(
    path: str = Query(..., min_length=1, max_length=512),
    device=Depends(rate_limited("vault_reads")),
    request: Request = None,
) -> list[BacklinkHit]:
    """Return notes that wikilink to `path`.

    Matches both `[[Title Words]]` (the human form) and the slug form
    `[[slug-words]]` so backlinks survive whether Hive / the user
    typed the human title or the filename. Audience-filtered.
    """
    st = state(request)
    root = st.config.vault_path.resolve()
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="path escapes vault") from e
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")

    # Build the candidate display names this note can be linked by.
    target_title = _title_for(st.config.vault_path, path) or target.stem
    candidates_lc = {
        target_title.lower(),
        target.stem.lower(),
    }

    audience = device.audience[0] if device.audience else "all"
    try:
        from vault_writer.util import audience_matches, parse_frontmatter
    except Exception:
        return []

    out: list[BacklinkHit] = []
    for p in st.config.vault_path.rglob("*.md"):
        try:
            rel_parts = p.relative_to(st.config.vault_path).parts
        except ValueError:
            continue
        if any(seg.startswith(".") for seg in rel_parts):
            continue
        rel = "/".join(rel_parts)
        if rel == path:
            continue
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            fm, body = parse_frontmatter(raw)
        except Exception:
            fm, body = {}, raw
        if not audience_matches(audience, list(fm.get("audience") or ["all"])):
            continue
        # Look for any [[Foo]] in the body whose normalized form matches
        # one of the target's candidate names. Case-insensitive.
        found = False
        for m in re.finditer(r"\[\[([^\]\|#]+)(?:[#\|][^\]]*)?\]\]", body):
            link = m.group(1).strip().lower()
            # Last-segment match too — `[[folder/foo]]` should backlink
            # if any candidate is "foo".
            link_tail = link.rsplit("/", 1)[-1]
            if link in candidates_lc or link_tail in candidates_lc:
                found = True
                break
        if not found:
            continue
        preview = body.strip().replace("\n", " ")
        if len(preview) > 200:
            preview = preview[:200] + "..."
        out.append(BacklinkHit(
            path=rel,
            title=_title_for(st.config.vault_path, rel),
            preview=preview,
        ))
    return out


# --- Related (semantic neighbours) -------------------------------------------


class RelatedHit(BaseModel):
    path: str
    title: str | None = None
    score: float
    preview: str


@router.get("/related", response_model=list[RelatedHit])
def vault_related(
    path: str = Query(..., min_length=1, max_length=512),
    k: int = Query(default=5, ge=1, le=20),
    device=Depends(rate_limited("vault_reads")),
    request: Request = None,
) -> list[RelatedHit]:
    """Top-k notes most semantically similar to `path`."""
    st = state(request)
    client = _vault_client(st)
    audience = device.audience[0] if device.audience else "all"
    neighbours = client.neighbours(path, k=k, audience=audience)
    out: list[RelatedHit] = []
    for r in neighbours:
        preview = r.body.strip().replace("\n", " ")
        if len(preview) > 200:
            preview = preview[:200] + "..."
        out.append(RelatedHit(
            path=r.path,
            title=_title_for(st.config.vault_path, r.path),
            score=round(r.score, 4),
            preview=preview,
        ))
    return out


# --- Tags ---------------------------------------------------------------------


class TagInfo(BaseModel):
    tag: str
    count: int


@router.get("/tags", response_model=list[TagInfo])
def vault_tags(
    device=Depends(rate_limited("vault_reads")),
    request: Request = None,
) -> list[TagInfo]:
    """Tag → note-count, descending. Audience-filtered."""
    st = state(request)
    audience = device.audience[0] if device.audience else "all"
    try:
        from vault_writer.util import audience_matches, parse_frontmatter
    except Exception:
        return []
    counts: dict[str, int] = {}
    for p in st.config.vault_path.rglob("*.md"):
        try:
            rel_parts = p.relative_to(st.config.vault_path).parts
        except ValueError:
            continue
        if any(seg.startswith(".") for seg in rel_parts):
            continue
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            fm, _b = parse_frontmatter(raw)
        except Exception:
            continue
        if not audience_matches(audience, list(fm.get("audience") or ["all"])):
            continue
        tags = fm.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, list):
            continue
        for t in tags:
            if isinstance(t, str) and t.strip():
                k = t.strip()
                counts[k] = counts.get(k, 0) + 1
    return [
        TagInfo(tag=t, count=c)
        for t, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


@router.get("/by-tag", response_model=list[BacklinkHit])
def vault_by_tag(
    tag: str = Query(..., min_length=1, max_length=64),
    device=Depends(rate_limited("vault_reads")),
    request: Request = None,
) -> list[BacklinkHit]:
    """Notes whose frontmatter `tags` include `tag` (case-insensitive)."""
    st = state(request)
    audience = device.audience[0] if device.audience else "all"
    try:
        from vault_writer.util import audience_matches, parse_frontmatter
    except Exception:
        return []
    needle = tag.strip().lower()
    out: list[BacklinkHit] = []
    for p in st.config.vault_path.rglob("*.md"):
        try:
            rel_parts = p.relative_to(st.config.vault_path).parts
        except ValueError:
            continue
        if any(seg.startswith(".") for seg in rel_parts):
            continue
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            fm, body = parse_frontmatter(raw)
        except Exception:
            fm, body = {}, raw
        if not audience_matches(audience, list(fm.get("audience") or ["all"])):
            continue
        tags = fm.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, list):
            continue
        if needle not in {str(t).strip().lower() for t in tags if isinstance(t, str)}:
            continue
        rel = "/".join(rel_parts)
        preview = body.strip().replace("\n", " ")
        if len(preview) > 200:
            preview = preview[:200] + "..."
        out.append(BacklinkHit(
            path=rel,
            title=_title_for(st.config.vault_path, rel),
            preview=preview,
        ))
    return out


# --- Learn (write via daemon) --------------------------------------------------

class LearnRequest(BaseModel):
    category: str = Field(..., min_length=1, max_length=32)
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=32 * 1024)
    audience: list[str] | None = None
    tags: list[str] | None = None
    extra: dict | None = None


class LearnResult(BaseModel):
    ok: bool
    path: str
    created: bool


@router.post("/learn", response_model=LearnResult)
async def learn(
    body: LearnRequest,
    device=Depends(rate_limited("writes")),
    request: Request = None,
) -> LearnResult:
    st = state(request)
    from gateway.action_executor import autolink_body
    from gateway.vault_quality import evaluate as _qa_eval

    # Quality gate — same threshold the synthesizer's vault_learn path
    # uses, so devices and Hive are held to the same bar.
    verdict = _qa_eval(title=body.title, body=body.body, category=body.category)
    if not verdict.ok:
        raise HTTPException(
            status_code=422,
            detail=f"below quality threshold: {verdict.reason}",
        )

    client = _vault_client(st)
    # Audience clamp — mirrors ActionExecutor._vault_learn so a
    # caller can't widen its own scope by passing an audience the
    # device isn't paired with. A 'claude-code' device that posts
    # `audience=['hive']` gets that intersected to its own
    # audience (here: empty → falls back to device.audience).
    requested = body.audience or list(device.audience) or ["all"]
    device_audience = list(device.audience) if device.audience else ["all"]
    if "all" in device_audience:
        audience = requested
    else:
        intersected = [a for a in requested if a in device_audience]
        audience = intersected or device_audience
    # Auto-link: same behaviour as ActionExecutor._vault_learn — wrap
    # mentions of existing audience-permitted note titles in
    # `[[wikilinks]]` so user-initiated saves also get the smart-
    # linking the user asked for.
    enriched_body, _linked = autolink_body(
        body.body, vault_path=st.config.vault_path,
        audience=audience, exclude_title=body.title,
    )
    resp = await client.learn(
        category=body.category,
        title=body.title,
        body=enriched_body,
        author="claude-code",
        audience=audience,
        tags=body.tags or [],
        extra=body.extra or {},
    )
    if resp is None:
        raise HTTPException(status_code=503, detail="vault-writer unreachable")
    if "error" in resp:
        raise HTTPException(status_code=400, detail=resp["error"])
    return LearnResult(ok=bool(resp.get("ok")), path=resp["path"], created=bool(resp.get("created")))
