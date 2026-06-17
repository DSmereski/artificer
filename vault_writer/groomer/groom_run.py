# vault_writer/groomer/groom_run.py
"""Orchestrate one grooming pass: run scanners, write suggestions.

Mirrors `gateway/auditor/audit_run.py`. Each scanner runs in its own
try/except so a single crash can never block the others. Auto-fixes
run AFTER scanners (so format_scanner sees the unfixed body).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from pathlib import Path
from typing import Any, Callable

from vault_writer.groomer.auto_fixers import apply_auto_fixes
from vault_writer.groomer.scanners import ScanContext, default_scanners
from vault_writer.groomer.suggestion import (
    KINDS,
    MAX_SUGGESTIONS_PER_RUN,
    Suggestion,
)
from vault_writer.groomer.suggestions_writer import write_suggestions

log = logging.getLogger("vault_writer.groomer.groom_run")


async def _run_one_scanner(
    scanner: Callable[..., Any], ctx: ScanContext,
) -> list[Suggestion]:
    try:
        result = scanner(ctx)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, list):
            log.warning("scanner %s returned non-list", getattr(scanner, "kind", "?"))
            return []
        return result
    except Exception:  # noqa: BLE001
        log.exception("scanner %s crashed", getattr(scanner, "kind", "?"))
        return []


def _open_embedder_or_none(vault_path: Path) -> Any:
    """Open an Ollama-backed Embedder + httpx client for the groomer pass.

    Returns ``(embedder, client)`` or ``None`` if anything is missing —
    no vault.db (so we can't probe the configured dimension), no httpx,
    or no embedder module. Returning a tuple lets ``run_groom`` close
    the underlying httpx.AsyncClient in its ``finally``; we don't use
    a context manager because the embedder needs to outlive the helper
    call site.

    The OLLAMA_HOST env var (with a localhost fallback) and the
    ``nomic-embed-text`` model match every other vault-writer caller
    (gateway/contradiction_detector.py, gateway/routes/vault.py,
    gateway/helpers/librarian.py). If those drift, embedding-using
    scanners would silently disagree with the rest of the system.
    """
    import os
    db_path = vault_path / ".vault-writer" / "vault.db"
    if not db_path.exists():
        return None
    try:
        import httpx
        from shared.vault_client import _probe_vec_dimension
        from vault_writer.embedder import Embedder
    except ImportError:
        return None
    dim = _probe_vec_dimension(db_path)
    if dim is None:
        return None
    ollama_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    try:
        client = httpx.AsyncClient(base_url=ollama_url, timeout=30.0)
    except Exception:  # noqa: BLE001
        log.exception("groomer could not open httpx client for Ollama")
        return None
    embedder = Embedder(client=client, model="nomic-embed-text", dimension=dim)
    return embedder, client


def _open_vault_index_or_none(vault_path: Path) -> Any:
    """Open a read-only VaultIndex handle for the groomer pass.

    Returns None if the index doesn't exist yet (fresh vault, daemon
    never ran) or sqlite_vec/the index module fails to load. Scanners
    that need the index already no-op when ctx.vault_index is None,
    so a missing handle is a graceful degradation rather than a crash.

    The handle is owned by run_groom and closed in the finally; do NOT
    cache it across runs — a long-running gateway would otherwise hold
    the SQLite file open even when the groomer isn't actively scanning.
    """
    db_path = vault_path / ".vault-writer" / "vault.db"
    if not db_path.exists():
        return None
    try:
        from vault_writer.index import VaultIndex
        from shared.vault_client import _probe_vec_dimension
    except ImportError:
        return None
    dim = _probe_vec_dimension(db_path)
    if dim is None:
        return None
    try:
        return VaultIndex.open(db_path, dimension=dim)
    except Exception:  # noqa: BLE001
        log.exception("groomer could not open VaultIndex for read")
        return None


async def run_groom(
    *,
    vault_path: Path,
    scanners: list[Callable[..., Any]] | None = None,
    vault_index: Any = None,
    embedder: Any = None,
    apply_auto: bool = False,
) -> dict[str, int]:
    """Run one grooming pass. Returns counts by kind.

    If `vault_index` is None we open one ourselves from the standard
    `<vault>/.vault-writer/vault.db` location and close it before
    returning. The auto-open path is what makes dup_scanner non-inert
    in production — without it the scanner has no embeddings to compare.
    """
    if scanners is None:
        scanners = default_scanners()
    owns_index = False
    if vault_index is None:
        vault_index = _open_vault_index_or_none(vault_path)
        owns_index = vault_index is not None
    owns_embedder = False
    embedder_client: Any = None
    if embedder is None:
        opened = _open_embedder_or_none(vault_path)
        if opened is not None:
            embedder, embedder_client = opened
            owns_embedder = True
    try:
        ctx = ScanContext(
            vault_path=vault_path,
            now_ts=time.time(),
            vault_index=vault_index,
            embedder=embedder,
        )
        all_suggestions: list[Suggestion] = []

        for sc in scanners:
            produced = await _run_one_scanner(sc, ctx)
            all_suggestions.extend(produced)

        # Global per-run cap: keep the highest-confidence proposals so the
        # user sees actionable items first when N scanners overproduce.
        if len(all_suggestions) > MAX_SUGGESTIONS_PER_RUN:
            all_suggestions.sort(key=lambda s: s.confidence, reverse=True)
            dropped = len(all_suggestions) - MAX_SUGGESTIONS_PER_RUN
            log.info("groom_run dropping %d low-confidence proposals over cap", dropped)
            all_suggestions = all_suggestions[:MAX_SUGGESTIONS_PER_RUN]

        counts: dict[str, int] = {k: 0 for k in KINDS}
        # Bucket by actual emitted kind (a scanner is allowed to emit multiple kinds).
        for s in all_suggestions:
            counts[s.kind] = counts.get(s.kind, 0) + 1

        write_suggestions(
            vault_path=vault_path,
            suggestions=all_suggestions,
            now_ts=ctx.now_ts,
            counts_by_kind=counts,
        )

        if apply_auto:
            try:
                apply_auto_fixes(vault_path)
            except Exception:  # noqa: BLE001
                log.exception("auto_fixes pass failed")

        return counts
    finally:
        if owns_index and vault_index is not None:
            try:
                vault_index.close()
            except Exception:  # noqa: BLE001
                log.exception("groomer failed to close auto-opened VaultIndex")
        if owns_embedder and embedder_client is not None:
            try:
                await embedder_client.aclose()
            except Exception:  # noqa: BLE001
                log.exception("groomer failed to close auto-opened httpx client")
