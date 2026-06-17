"""Quality gate for vault writes.

Per the standing directive: Claude and Terry should add to the vault
when they have knowledge worth saving — but the writes get reviewed
against a threshold first, and below-threshold attempts are skipped
rather than polluting the index.

The check is deliberately cheap (no embeddings, no LLM round-trip): a
deterministic scorer that catches the failure modes seen in the
2026-04-28 turn logs:

  - Researcher saving notes that are 80% URLs because nothing got
    extracted.
  - Synthesizer saving very short stubs ("X is a thing.") with no
    actual information.
  - Notes whose body is mostly stop-words / boilerplate.

Returning ``QualityVerdict(ok=True)`` lets the write proceed unchanged.
``ok=False`` means the caller should refuse with the supplied reason
(receipt detail, HTTP 422, etc.).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# Tunable thresholds — exposed as module attributes so tests can patch.
MIN_BODY_CHARS = 80           # Drops "X is a thing." stubs.
MIN_INFO_TOKENS = 12          # Alphanumeric tokens (≥2 chars).
MIN_INFO_RATIO = 0.30         # Info chars / total chars; drops link-heavy bodies.
MIN_TITLE_TOKENS = 1          # Permissive — slugs are short by design.


_WORD_RE = re.compile(r"[A-Za-z0-9]{2,}")
_URL_RE = re.compile(r"https?://\S+")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


@dataclass(frozen=True, slots=True)
class QualityVerdict:
    ok: bool
    reason: str = ""
    score: dict[str, float] | None = None


def evaluate(*, title: str, body: str, category: str = "knowledge") -> QualityVerdict:
    """Evaluate a proposed vault write. Pure / deterministic.

    Append-style categories (journal/session/person) skip the gate —
    those accumulate over time and per-write noise is fine. Recipes
    and references are also exempt because their value is the
    structured extra-fields, not the body prose.
    """
    if category in ("journal", "session", "person", "reference"):
        return QualityVerdict(ok=True, reason="exempt category")

    title_tokens = _WORD_RE.findall(title or "")
    if len(title_tokens) < MIN_TITLE_TOKENS:
        return QualityVerdict(
            ok=False,
            reason=f"title has no informative tokens",
        )

    body = (body or "").strip()
    if len(body) < MIN_BODY_CHARS:
        return QualityVerdict(
            ok=False,
            reason=f"body too short ({len(body)} chars < {MIN_BODY_CHARS})",
            score={"body_chars": float(len(body))},
        )

    # Strip URLs and code fences before counting "info" — a wall of
    # links isn't knowledge, and code blocks distort the ratio in the
    # other direction (they're informative but token-sparse).
    info_text = _URL_RE.sub(" ", _FENCE_RE.sub(" ", body))
    info_tokens = _WORD_RE.findall(info_text)
    if len(info_tokens) < MIN_INFO_TOKENS:
        return QualityVerdict(
            ok=False,
            reason=(
                f"too few informative tokens "
                f"({len(info_tokens)} < {MIN_INFO_TOKENS}); body looks like a "
                f"link list or near-empty stub"
            ),
            score={
                "info_tokens": float(len(info_tokens)),
                "body_chars": float(len(body)),
            },
        )

    info_chars = sum(len(t) for t in info_tokens)
    ratio = info_chars / max(len(body), 1)
    if ratio < MIN_INFO_RATIO:
        return QualityVerdict(
            ok=False,
            reason=(
                f"informative-content ratio too low ({ratio:.2f} < "
                f"{MIN_INFO_RATIO:.2f}); body is dominated by URLs or "
                f"punctuation"
            ),
            score={"info_ratio": round(ratio, 3)},
        )

    return QualityVerdict(
        ok=True,
        score={
            "body_chars": float(len(body)),
            "info_tokens": float(len(info_tokens)),
            "info_ratio": round(ratio, 3),
        },
    )
