# vault_writer/groomer/scanners/dup_scanner.py
"""dup_scanner — flags note pairs with embedding cosine > 0.92.

Reuses the embeddings already produced by `vault_writer.embed_worker`
for the FTS+vec hybrid search. We don't re-embed; we read what's
indexed.
"""
from __future__ import annotations

import math
from typing import Any

from vault_writer.groomer.scanners import ScanContext
from vault_writer.groomer.suggestion import (
    MAX_SUGGESTIONS_PER_SCAN,
    Suggestion,
)


name = "dup_scanner"
kind = "dup_scanner"

_THRESHOLD = 0.92


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _slug(path_a: str, path_b: str) -> str:
    """Pair slug stable across run order: alphabetised stems joined."""
    from pathlib import PurePosixPath
    sa = PurePosixPath(path_a).stem
    sb = PurePosixPath(path_b).stem
    a, b = sorted([sa, sb])
    return f"{a}__{b}"


def scan(ctx: ScanContext) -> list[Suggestion]:
    idx = ctx.vault_index
    if idx is None or not hasattr(idx, "list_note_embeddings"):
        return []
    try:
        rows = list(idx.list_note_embeddings())
    except Exception:  # noqa: BLE001
        return []
    out: list[Suggestion] = []
    for i in range(len(rows)):
        if len(out) >= MAX_SUGGESTIONS_PER_SCAN:
            break
        for j in range(i + 1, len(rows)):
            if len(out) >= MAX_SUGGESTIONS_PER_SCAN:
                break
            pa, ea = rows[i]
            pb, eb = rows[j]
            cos = _cosine(ea, eb)
            if cos < _THRESHOLD:
                continue
            body = (
                f"Embedding cosine: **{cos:.3f}**\n\n"
                f"- `{pa}`\n"
                f"- `{pb}`\n\n"
                "## Recommended action\n"
                "Review for merge — older file's unique content should be\n"
                "pulled into the newer file, then the older file deleted.\n"
            )
            out.append(Suggestion(
                kind="dup_scanner",
                slug=_slug(pa, pb),
                confidence=min(1.0, cos),
                title=f"Possible duplicate: {pa} ↔ {pb}",
                body_md=body,
                refs=(pa, pb),
            ))
    return out


scan.kind = "dup_scanner"
scan.name = "dup_scanner"
