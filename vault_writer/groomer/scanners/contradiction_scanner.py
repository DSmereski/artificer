# vault_writer/groomer/scanners/contradiction_scanner.py
"""contradiction_scanner — entity_page.compiled_truth vs recent timeline.

When the user updates an entity_page (via core_memory_replace or the
synthesizer's entity_page_update verb), the new truth lands in
`compiled_truth`. The timeline keeps an immutable record of every
mention. If the truth diverges from what was recently appended, that
suggests either a compounding of stale info or a real correction —
in either case it deserves human review.

The scanner embeds compiled_truth and the most-recent timeline entry
on-the-fly via `ctx.embedder` rather than relying on persisted vectors,
because entity_page rows have no embedding column today and adding one
would require a write-path round-trip and a migration. Stays inert
when no embedder is supplied (production currently relies on the
write-time `gateway.contradiction_detector` for the primary signal —
auto-opening an Embedder in run_groom is a separate follow-up).
"""
from __future__ import annotations

import logging
import math

from vault_writer.groomer.scanners import ScanContext
from vault_writer.groomer.suggestion import (
    MAX_SUGGESTIONS_PER_SCAN,
    Suggestion,
)


log = logging.getLogger("vault_writer.groomer.contradiction_scanner")

name = "contradiction_scanner"
kind = "contradiction_scanner"

_THRESHOLD = 0.6  # below = divergent


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


async def scan(ctx: ScanContext) -> list[Suggestion]:
    idx = ctx.vault_index
    embedder = ctx.embedder
    if idx is None or embedder is None:
        return []
    if not hasattr(idx, "list_entity_pages_for_contradiction_scan"):
        return []
    try:
        rows = list(idx.list_entity_pages_for_contradiction_scan())
    except Exception:  # noqa: BLE001
        log.exception("list_entity_pages_for_contradiction_scan failed")
        return []
    out: list[Suggestion] = []
    for row in rows:
        if len(out) >= MAX_SUGGESTIONS_PER_SCAN:
            break
        truth = row.get("compiled_truth") or ""
        recent = row.get("recent_timeline_entry") or ""
        if not truth or not recent:
            continue
        try:
            ce = await embedder.embed(truth)
            te = await embedder.embed(recent)
        except Exception:  # noqa: BLE001
            # One bad embed shouldn't kill the whole scan — Ollama
            # blips are common during idle passes and the next pass
            # will retry.
            log.info("embedder failed for entity %s", row.get("id"))
            continue
        cos = _cosine(ce, te)
        if cos >= _THRESHOLD:
            continue
        ent_id = row.get("id") or "unknown"
        title = row.get("title") or ent_id
        body = (
            f"Entity: **{title}** (`{ent_id}`)\n"
            f"compiled_truth ↔ recent timeline cosine: **{cos:.3f}**\n\n"
            "## Recommended action\n"
            "Review the entity page; either reconcile compiled_truth with\n"
            "the recent timeline or amend the timeline if it's wrong.\n"
        )
        # Confidence rises as cosine falls.
        confidence = max(0.0, min(1.0, 1.0 - cos))
        out.append(Suggestion(
            kind="contradiction_scanner",
            slug=str(ent_id).replace("/", "_").replace(" ", "-"),
            confidence=confidence,
            title=f"Contradiction: {title}",
            body_md=body,
            refs=(f"entity:{ent_id}",),
        ))
    return out


scan.kind = "contradiction_scanner"
scan.name = "contradiction_scanner"
