"""Generate canon/imagegen-loras.md from the imageToVideo LoRA registry.

Why this exists: Hive's system prompt used to dump all 76 active LoRAs
inline (~1500 chars). That's wasteful — the catalog rarely changes within
a session and Hive only needs LoRA detail when she's actually building
an image prompt. Better to write the catalog as a vault note Hive can
reach for via vault search at chat-time.

Idempotent: only rewrites when `lora_registry.json` is newer than the
existing markdown note. Safe to call on every gateway startup.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import Iterable

log = logging.getLogger("gateway.image_lora_doc")


_FRONTMATTER = """\
---
type: canon
author: claude-code
audience: [hive, claude-code]
tags: [image-gen, loras, catalog]
source: $HIVE_IMAGE_BACKEND_PATH/models/loras/lora_registry.json
---
"""


_PIPELINE_LABEL = {
    "flux": "FLUX",
    "sdxl": "SDXL / Pony",
    "sd15": "SD 1.5",
    "wan": "WAN (video)",
    "unknown": "Unknown",
}


_PIPELINE_HINT = {
    "flux": (
        "FLUX speaks natural language. Stack at most 2 LoRAs; FLUX is sensitive "
        "to LoRA over-stacking. Skip negative prompts."
    ),
    "sdxl": (
        "SDXL/Pony rewards tag-style prompts and benefits from a strong negative "
        "prompt. The score_9 / score_8_up / score_7_up tags help on Pony models."
    ),
    "sd15": (
        "SD 1.5 — older, smaller. Use only when a LoRA is SD-1.5-specific."
    ),
    "wan": "WAN LoRAs are video-focused; don't use for still images.",
    "unknown": "Pipeline unconfirmed — try sparingly.",
}


# Categorical hints — when the user asks for X, reach for these LoRAs.
_CATEGORY_USAGE = {
    "people":      "User asks for portraits, selfies, real people, or photo-realistic faces.",
    "enhancement": "User wants quality polish — apply with low strength alongside a primary LoRA.",
    "style":       "User asks for a specific aesthetic or art style (cyberpunk, oil painting, etc.).",
    "fantasy":     "User asks for fantasy / sci-fi / mythical scenes.",
    "restricted":  "Restricted-category LoRAs — filtered out by default; only when the user explicitly asks.",
    "":            "General-purpose. Match by trigger words.",
}


def _normalize_loras(raw: list[dict]) -> list[dict]:
    """Take the raw registry list and produce stable, sorted entries."""
    out = []
    for entry in raw:
        alias = str(entry.get("alias") or "").strip()
        if not alias:
            continue
        out.append({
            "alias": alias,
            "pipeline": str(entry.get("pipeline") or "unknown").lower(),
            "trigger_words": str(entry.get("trigger_words") or "").strip(),
            "default_strength": float(entry.get("default_strength", 1.0)),
            "category": str(entry.get("category") or "").strip(),
            "nsfw": bool(entry.get("nsfw", False)),
        })
    out.sort(key=lambda e: (e["pipeline"], e["alias"].lower()))
    return out


def _format_row(lora: dict) -> str:
    alias = lora["alias"]
    triggers = lora["trigger_words"]
    strength = lora["default_strength"]
    category = lora["category"] or "—"
    nsfw_flag = " 🔒" if lora["nsfw"] else ""
    triggers_cell = triggers if triggers else "—"
    return f"| {alias}{nsfw_flag} | {category} | {strength:g} | {triggers_cell} |"


def _group_by_pipeline(loras: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for l in loras:
        groups[l["pipeline"]].append(l)
    return groups


def render_catalog(loras: Iterable[dict]) -> str:
    """Render the full registry as a Markdown canon note."""
    normalized = _normalize_loras(list(loras))
    groups = _group_by_pipeline(normalized)
    nsfw_count = sum(1 for l in normalized if l["nsfw"])

    lines = [_FRONTMATTER, "", "# LoRA Catalog"]
    lines.append("")
    lines.append(
        f"All LoRAs Hive can use, grouped by pipeline. "
        f"**{len(normalized)} total**, {nsfw_count} restricted (🔒 — request explicitly only)."
    )
    lines.append("")
    lines.append(
        "Reach for one by passing `\"loras\": [\"<alias>\"]` in a "
        "`[GENERATE_IMAGE]` payload. Auto-pick selects from this same list "
        "when no LoRAs are provided."
    )
    lines.append("")

    # Stable order of pipelines.
    for pipeline_key in ("flux", "sdxl", "sd15", "wan", "unknown"):
        bucket = groups.get(pipeline_key, [])
        if not bucket:
            continue
        label = _PIPELINE_LABEL.get(pipeline_key, pipeline_key.upper())
        hint = _PIPELINE_HINT.get(pipeline_key, "")
        lines.append(f"## {label} ({len(bucket)})")
        lines.append("")
        if hint:
            lines.append(f"_{hint}_")
            lines.append("")
        lines.append("| LoRA | Category | Strength | Trigger words |")
        lines.append("|---|---|---|---|")
        for lora in bucket:
            lines.append(_format_row(lora))
        lines.append("")

    lines.append("## When to reach for which category")
    lines.append("")
    cats = sorted({l["category"] or "" for l in normalized})
    for cat in cats:
        hint = _CATEGORY_USAGE.get(cat, _CATEGORY_USAGE[""])
        lines.append(f"- **{cat or '(uncategorized)'}** — {hint}")
    lines.append("")
    lines.append("## Picking notes")
    lines.append("")
    lines.append(
        "- **Stack 1–2 LoRAs at most**, especially on FLUX. Stacking 3+ degrades "
        "quality fast."
    )
    lines.append(
        "- LoRA pipeline must match the active model. SDXL LoRAs on FLUX = "
        "garbage; auto-pick filters this for you, but check when you stack manually."
    )
    lines.append(
        "- Strength <1.0 dilutes the LoRA's effect; >1.0 oversaturates. Start at "
        "the default strength and adjust on a redo."
    )
    lines.append(
        "- Restricted LoRAs (🔒) are filtered out of the visible catalog by default. "
        "Only surface them when the user explicitly asks."
    )
    lines.append(
        "- When the user says 'realistic' / 'photorealistic' / 'photograph', try "
        "**Real Beauty** + **Lighting Slider** if the active model is SDXL/Pony, "
        "or skip LoRAs and let FLUX handle it natively."
    )
    return "\n".join(lines).rstrip() + "\n"


def regenerate_if_stale(
    *,
    registry_path: Path,
    canon_path: Path,
) -> tuple[bool, int]:
    """Rewrite `canon_path` from `registry_path` if registry is newer.

    Returns (rewrote, lora_count). Never raises: a missing registry or
    write failure is logged and returns (False, 0).
    """
    try:
        if not registry_path.exists():
            log.info("lora registry not found at %s; skipping catalog write", registry_path)
            return (False, 0)
        registry_mtime = registry_path.stat().st_mtime
        canon_mtime = canon_path.stat().st_mtime if canon_path.exists() else 0.0
        # Always rewrite if the canon doesn't exist; otherwise only if registry
        # is meaningfully newer (1s tolerance to dodge filesystem clock drift).
        if canon_path.exists() and registry_mtime <= canon_mtime + 1.0:
            log.debug("lora catalog up-to-date (%s)", canon_path)
            # Count for return value.
            try:
                import json
                count = len(json.loads(registry_path.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                count = 0
            return (False, count)

        import json
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            log.warning("lora registry is not a JSON list; skipping")
            return (False, 0)
        body = render_catalog(raw)
        canon_path.parent.mkdir(parents=True, exist_ok=True)
        # Write atomically: tmp then rename.
        tmp = canon_path.with_suffix(canon_path.suffix + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, canon_path)
        log.info("lora catalog regenerated (%d LoRAs) at %s", len(raw), canon_path)
        return (True, len(raw))
    except Exception as e:  # noqa: BLE001
        log.warning("lora catalog regeneration failed: %s", e)
        return (False, 0)
