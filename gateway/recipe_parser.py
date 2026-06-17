"""Civitai image-recipe text parser.

A "recipe" is the prompt block users paste from a Civitai image page —
positive prompt, `Negative prompt: ...`, plus a `Steps: N, CFG: X,
Sampler: Y` settings line, plus optional Civitai model URLs scattered
through "Resources used" sections.

Pulled out of `asset_importer.py` (1193-LoC monolith the analyst's
2026-04-29 review flagged) so the parser can grow regression tests
without dragging the SSRF guard, downloader, and registry installer
into every test fixture. Pure functions, no I/O.
"""

from __future__ import annotations

import re


_RECIPE_TEXT_CAP = 32 * 1024     # 32 KB; longer pastes are pathological
_NEG_PROMPT_RE = re.compile(
    r"Negative prompt:\s*(.*?)(?=\n(?:Steps|Sampler|CFG|Seed|Model)\s*[:=]|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_STEPS_RE = re.compile(r"\bSteps:\s*(\d+)", re.IGNORECASE)
_CFG_RE = re.compile(r"\bCFG\s*scale:\s*([\d.]+)", re.IGNORECASE)
_SAMPLER_RE = re.compile(r"\bSampler:\s*([A-Za-z0-9_+\-]+)", re.IGNORECASE)
_SEED_RE = re.compile(r"\bSeed:\s*(-?\d+)", re.IGNORECASE)
_MODEL_URL_RE = re.compile(
    r"https?://civitai\.(?:com|red)/models/\d+(?:/[^\s?]*)?(?:\?modelVersionId=\d+)?",
    re.IGNORECASE,
)


_VIDEO_HINTS = (
    "wan22", "wan-22", "wan 22", "wan2.2",
    "i2v", "v2v", "t2v",
    "ltx2", "ltx-2", "ltx 2",
    "hunyuanvideo", "cogvideo", "cogvideox",
    "animatediff",
    "wanvideo",
)
# The exact Chinese-language WAN default-negative-prompt fragment;
# its presence is a strong "this is a WAN video recipe" signal.
_VIDEO_NEG_HINT = "色调艳丽，过曝，静态"


def detect_recipe_kind(text: str, model_urls: list[str]) -> str:
    """Return "video" if the recipe smells like a video pipeline,
    else "still". Heuristics over the pasted text + URL slugs."""
    blob = (text or "").lower()
    if any(h in blob for h in _VIDEO_HINTS):
        return "video"
    if _VIDEO_NEG_HINT in (text or ""):
        return "video"
    for u in model_urls:
        u_lower = u.lower()
        if any(h in u_lower for h in _VIDEO_HINTS):
            return "video"
    return "still"


def parse_civitai_recipe_text(text: str) -> dict:
    """Extract positive/negative/sampler/steps/cfg/seed/model_urls from
    a pasted Civitai image-page block.

    The block typically looks like:

        <positive prompt text>
        Negative prompt: <negative text>
        Steps: 7, CFG scale: 5, Sampler: Euler

    plus optional model URLs in "Resources used" sections. Returns
    None for fields not found. `kind: "still" | "video"` is heuristic.
    """
    if not isinstance(text, str):
        return {
            "positive": "", "negative": None,
            "sampler": None, "steps": None, "cfg": None, "seed": None,
            "model_urls": [],
        }
    text = text.strip()[:_RECIPE_TEXT_CAP]

    neg_match = _NEG_PROMPT_RE.search(text)
    negative: str | None = None
    if neg_match:
        negative = neg_match.group(1).strip().rstrip(",").strip() or None

    # Positive = everything before "Negative prompt:" or first settings
    # line, whichever comes first. Fallback: whole text.
    cuts: list[int] = []
    if neg_match:
        cuts.append(neg_match.start())
    for r in (_STEPS_RE, _SAMPLER_RE, _CFG_RE, _SEED_RE):
        m = r.search(text)
        if m:
            cuts.append(m.start())
    positive_end = min(cuts) if cuts else len(text)
    positive = text[:positive_end].strip()

    sampler = (m.group(1) if (m := _SAMPLER_RE.search(text)) else None)
    steps_m = _STEPS_RE.search(text)
    cfg_m = _CFG_RE.search(text)
    seed_m = _SEED_RE.search(text)

    # Dedup model URLs preserving order.
    seen_urls: set[str] = set()
    model_urls: list[str] = []
    for u in _MODEL_URL_RE.findall(text):
        norm = u.strip()
        if norm not in seen_urls:
            seen_urls.add(norm)
            model_urls.append(norm)

    return {
        "positive": positive,
        "negative": negative,
        "sampler": sampler,
        "steps": int(steps_m.group(1)) if steps_m else None,
        "cfg": float(cfg_m.group(1)) if cfg_m else None,
        "seed": int(seed_m.group(1)) if seed_m else None,
        "model_urls": model_urls,
        "kind": detect_recipe_kind(text, model_urls),
    }
