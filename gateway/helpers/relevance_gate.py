"""Programmatic relevance gate for retrieval-helper outputs.

Defense-in-depth for the synthesizer's Rule 8b ("treat off-topic
helper output as empty"). The synthesizer prompt instructs the LLM
to ignore mismatched librarian/researcher hits, but in production
data (2026-04-26..05-01) the LLM still occasionally rendered
obviously-irrelevant hits verbatim — e.g. "Drake Cutlass Black"
question answered with a 17th-century-dagger note.

Strategy: when a retrieval-style helper returns output that shares
zero salient tokens with the user's question, blank its output so
the synthesizer falls cleanly into Rule 8 ("librarian came back
empty"). Cheap, deterministic, no LLM call, no embedding.

Salient = lowercase ASCII tokens of length ≥4 that aren't common
stopwords. Tokens that are too short or too common can't carry
specificity, so omitting them avoids over-filtering on queries
like "what's that thing again?".

The gate is intentionally narrow: it ONLY fires on librarian and
researcher (the retrieval helpers most prone to off-topic noise).
chat_recall, sysmon, planner outputs pass through untouched.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

from gateway.helpers.base import HelperResult


log = logging.getLogger("gateway.helpers.relevance_gate")


# Roles whose output should be relevance-checked. Adding a new
# retrieval helper? Add it here.
_GATED_ROLES = frozenset({"librarian", "researcher"})

# Minimum query-token length that counts as salient. "the", "and",
# "you" can't carry topic-specificity; "drake", "cutlass" can.
_MIN_TOKEN_LEN = 4

# Stopwords that pass the length filter but still aren't salient
# enough to drive a relevance signal.
_STOPWORDS = frozenset({
    "what", "that", "this", "with", "from", "your", "have", "they",
    "them", "were", "been", "into", "some", "more", "than", "then",
    "when", "where", "which", "would", "could", "should", "about",
    "there", "their", "thing", "things", "really", "again", "also",
    "just", "like", "want", "need", "tell", "know", "make", "made",
    "take", "took", "give", "gave", "doing", "going", "show", "find",
    "look", "trying", "still",
    "actually", "basically", "literally", "honestly", "kinda",
    "stuff", "anything", "something", "nothing",
})

_TOKEN_RE = re.compile(r"[a-z][a-z0-9]+")

# ALL-CAPS sequences of ≥2 chars carry strong topic specificity
# even at 2-3 chars — GPU, RAM, PSU, CPU, SSD, USB, OS, AI, etc.
# Extracted from the ORIGINAL casing before lowercasing so they
# survive the _MIN_TOKEN_LEN filter that exists to suppress
# common 2-3 letter words ("the", "and", "you", "ram"-the-verb).
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,}\b")


def _salient_tokens(text: str) -> set[str]:
    if not text:
        return set()
    tokens = {
        t for t in _TOKEN_RE.findall(text.lower())
        if len(t) >= _MIN_TOKEN_LEN and t not in _STOPWORDS
    }
    for ac in _ACRONYM_RE.findall(text):
        tokens.add(ac.lower())
    return tokens


def _output_text(output: dict) -> str:
    """Flatten a helper output dict into a single searchable string.

    Includes summary, hits/paths/excerpts, facts, notes, citations —
    all the surfaces a synthesizer would otherwise rewrite into the
    reply. The `notes` key is a free-form annotation field emitted by
    some helper variants; omitting it caused relevant results whose
    topic signal lived only in notes to be incorrectly gated.
    """
    if not isinstance(output, dict):
        return ""
    parts: list[str] = []
    summary = output.get("summary")
    if isinstance(summary, str):
        parts.append(summary)
    notes = output.get("notes")
    if isinstance(notes, str):
        parts.append(notes)
    hits = output.get("hits")
    if isinstance(hits, list):
        for h in hits:
            if isinstance(h, dict):
                for k in ("path", "title", "excerpt", "body"):
                    v = h.get(k)
                    if isinstance(v, str):
                        parts.append(v)
    facts = output.get("facts")
    if isinstance(facts, list):
        for f in facts:
            if isinstance(f, dict):
                for k in ("claim", "text", "source"):
                    v = f.get(k)
                    if isinstance(v, str):
                        parts.append(v)
            elif isinstance(f, str):
                parts.append(f)
    citations = output.get("citations")
    if isinstance(citations, list):
        parts.extend(c for c in citations if isinstance(c, str))
    return " ".join(parts)


def filter_irrelevant(
    user_msg: str, helper_results: Iterable[HelperResult],
) -> list[HelperResult]:
    """Blank retrieval helpers whose output is obviously off-topic.

    Returns a new list — does NOT mutate the input. Each gated
    helper either passes through unchanged (relevant) or has its
    output replaced with an empty dict and confidence dropped to
    'low' so the synthesizer's Rule 8 path triggers cleanly.

    Falsy `user_msg` or queries with no salient tokens skip the
    gate entirely (can't compute a meaningful overlap signal).
    """
    results = list(helper_results)
    query_tokens = _salient_tokens(user_msg)
    if not query_tokens:
        return results

    # Minimum distinct-token overlap required to keep retrieval output.
    # A single overlap is too lenient when the query is information-
    # dense: 'Shimokitazawa cafe Wednesday morning coding' (5+
    # salient tokens) was keeping internal tooling docs because the
    # bare token 'coding' overlapped with 'Discord bot architecture'
    # notes. For dense queries (>=3 salient tokens) require >=2
    # distinct overlapping tokens so the hit covers more than one
    # facet of the question.
    #
    # For 1-2 salient tokens fall back to the original >=1 rule —
    # the user's question is narrow enough that even single-token
    # overlap on a distinctive term is meaningful.
    min_overlap = 2 if len(query_tokens) >= 3 else 1

    out: list[HelperResult] = []
    for r in results:
        if r.role not in _GATED_ROLES or r.error or not r.output:
            out.append(r)
            continue
        output_tokens = _salient_tokens(_output_text(r.output))
        overlap = query_tokens & output_tokens
        if len(overlap) >= min_overlap:
            out.append(r)
            continue
        # Below-threshold overlap — treat as off-topic. Blank the
        # output and flag confidence so the synthesizer reads it as
        # empty, not as authoritative content to render.
        log.info(
            "relevance gate dropped %s output (overlap=%s, need >=%d, "
            "query tokens=%s)",
            r.role, sorted(overlap), min_overlap, sorted(query_tokens)[:6],
        )
        gated = HelperResult(
            role=r.role,
            model_id=r.model_id,
            plan=r.plan,
            output={
                "summary": (
                    f"{r.role} returned hits but none matched the user's "
                    "question — treating as empty"
                ),
                "hits": [],
            },
            citations=[],
            confidence="low",
            tokens_in=r.tokens_in,
            tokens_out=r.tokens_out,
            latency_ms=r.latency_ms,
            error=None,
            parent_id=r.parent_id,
            raw_text=r.raw_text,
        )
        out.append(gated)
    return out
