"""Image-gen catalog for Terry.

Surfaces what the imageToVideo app already knows about LoRAs, aspect-ratio
shortcuts, and named presets, so:
  - Terry's system prompt can list what's available (she picks when asked)
  - The chat route can auto-pick LoRAs from a bare prompt (no explicit list)
  - The app can expose pickers (via GET /v1/images/catalog)

This module is a thin adapter. All the real logic (registry I/O, keyword
scoring, Qwen ranking, path resolution) lives in imageToVideo — don't
reimplement it here.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


log = logging.getLogger("gateway.image_catalog")


def parse_image_payload(raw: str) -> dict | None:
    """Normalise a '[GENERATE_IMAGE] ...' payload string.

    Returns dict with at least 'prompt', plus any of: aspect, preset, loras,
    negative, count, steps, guidance, enhance, model. None if empty.

    Accepts plain text OR a leading-'{' JSON object. JSON parse failure
    falls back to treating the whole thing as a plain prompt (never raises).
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            log.warning("image payload JSON parse failed (%s); using raw text", e)
            return {"prompt": raw}
        if not isinstance(obj, dict):
            return {"prompt": raw}
        prompt = str(obj.get("prompt", "")).strip()
        if not prompt:
            return None
        out: dict = {"prompt": prompt}
        for k in ("aspect", "preset", "negative", "model"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                out[k] = v.strip()
        loras = obj.get("loras")
        if isinstance(loras, list):
            out["loras"] = [str(x).strip() for x in loras if str(x).strip() or x == ""]
        for k, cast in (("count", int), ("steps", int), ("guidance", float)):
            v = obj.get(k)
            if isinstance(v, (int, float)):
                try:
                    out[k] = cast(v)
                except (TypeError, ValueError):
                    pass
        if isinstance(obj.get("enhance"), bool):
            out["enhance"] = obj["enhance"]
        return out
    return {"prompt": raw}


# Aspect-ratio shortcuts mirror `imageToVideo/chat/chat_tools.py::_ASPECT_RATIOS`.
# Copied here (not imported) because chat_tools imports its own heavy deps on
# module load. Keep in sync if that file changes.
ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "square": (1024, 1024),
    "portrait": (768, 1344),
    "landscape": (1344, 768),
    "ultrawide": (1536, 640),
    "wallpaper": (2560, 1440),
}


@dataclass(frozen=True, slots=True)
class LoraEntry:
    alias: str
    pipeline: str
    trigger_words: str
    default_strength: float
    category: str
    nsfw: bool


@dataclass(frozen=True, slots=True)
class PresetEntry:
    name: str
    category: str
    width: int
    height: int
    steps: int
    guidance: float
    negative: str
    loras: list[dict]  # [{choice, strength}, ...]
    nsfw: bool
    tags: list[str]


@dataclass
class ImageCatalog:
    """Snapshot of LoRAs + presets + aspects at a point in time."""
    loras: list[LoraEntry] = field(default_factory=list)
    presets: list[PresetEntry] = field(default_factory=list)
    aspects: dict[str, tuple[int, int]] = field(default_factory=lambda: dict(ASPECT_RATIOS))
    loaded: bool = False


def _ensure_image_app_on_path(image_app_root: Path | None) -> bool:
    """Make imageToVideo importable. Returns True on success."""
    if image_app_root is None:
        return False
    if not image_app_root.is_dir():
        return False
    root = str(image_app_root)
    if root not in sys.path:
        sys.path.insert(0, root)
    return True


def load_catalog(image_app_root: Path | None) -> ImageCatalog:
    """Load LoRAs + presets from the imageToVideo app.

    Never raises — a missing or broken catalog is logged and returns empty.
    """
    cat = ImageCatalog()
    if not _ensure_image_app_on_path(image_app_root):
        log.info("image_app_root not set/usable; catalog empty")
        return cat

    try:
        from browsing.model_browser import list_loras  # type: ignore[import-not-found]
    except Exception as e:  # noqa: BLE001
        log.warning("cannot import list_loras: %s", e)
        return cat

    try:
        raw_loras: list[dict[str, Any]] = list_loras()
    except Exception as e:  # noqa: BLE001
        log.warning("list_loras() failed: %s", e)
        raw_loras = []

    for entry in raw_loras:
        if not entry.get("exists", True):
            continue
        cat.loras.append(LoraEntry(
            alias=str(entry.get("alias", "")).strip(),
            pipeline=str(entry.get("pipeline", "unknown")).lower(),
            trigger_words=str(entry.get("trigger_words", "")).strip(),
            default_strength=float(entry.get("default_strength", 1.0)),
            category=str(entry.get("category", "")).strip(),
            nsfw=bool(entry.get("nsfw", False)),
        ))

    try:
        from presets.builtin import BUILTIN_PRESETS  # type: ignore[import-not-found]
    except Exception as e:  # noqa: BLE001
        log.warning("cannot import BUILTIN_PRESETS: %s", e)
        BUILTIN_PRESETS = {}  # noqa: N806

    for name, p in BUILTIN_PRESETS.items():
        cat.presets.append(PresetEntry(
            name=name,
            category=str(p.get("category", "general")),
            width=int(p.get("img_width", 1024)),
            height=int(p.get("img_height", 1024)),
            steps=int(p.get("img_steps", 20)),
            guidance=float(p.get("img_guidance", 3.5)),
            negative=str(p.get("img_negative", "")),
            loras=list(p.get("loras", []) or []),
            nsfw=bool(p.get("nsfw", False)),
            tags=list(p.get("tags", []) or []),
        ))

    cat.loaded = True
    return cat


def resolve_aspect(name: str | None) -> tuple[int, int] | None:
    if not name:
        return None
    return ASPECT_RATIOS.get(name.strip().lower())


def resolve_preset(catalog: ImageCatalog, name: str | None) -> PresetEntry | None:
    if not name:
        return None
    key = name.strip().lower()
    for p in catalog.presets:
        if p.name.lower() == key:
            return p
    return None


def pick_auto_loras(
    prompt: str,
    *,
    image_app_root: Path | None,
    model_choice: str | None,
    max_loras: int,
    catalog: ImageCatalog | None = None,
) -> list[dict]:
    """Auto-pick LoRAs for a prompt.

    Tries imageToVideo's `select_loras()` first; if that returns empty
    we fall through to a token-overlap heuristic against the local
    catalog (alias / trigger_words / category) so we don't ignore the
    user's installed LoRAs just because the upstream picker bailed.
    The user complaint: 'better use of all the loras' — every render
    was coming back with `loras: None` because the upstream picker
    filters too conservatively and we had no fallback.

    Returns [{"choice": "<display>", "strength": float}, ...] or [].
    Never raises.
    """
    primary: list[dict] = []
    if _ensure_image_app_on_path(image_app_root):
        try:
            from core.ai_generate import select_loras  # type: ignore[import-not-found]
            primary = select_loras(
                prompt, model_choice or "", max_loras=max_loras,
            ) or []
        except Exception as e:  # noqa: BLE001
            log.warning("select_loras failed (using fallback): %s", e)

    if primary:
        return primary
    if catalog is None or not catalog.loras:
        return []
    # Local fallback: score each LoRA by token overlap between the
    # prompt and (alias + trigger_words + category). Wins picked
    # without dragging the upstream picker into the loop.
    return _local_pick_loras(prompt, catalog, max_loras=max_loras,
                              model_choice=model_choice)


_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
_PROMPT_STOP = frozenset({
    "the", "a", "an", "and", "of", "to", "in", "on", "at", "by",
    "with", "for", "is", "are", "was", "were", "be", "been", "as",
    "it", "this", "that", "from", "or", "but", "not", "no", "so",
})


def _model_pipeline(model_choice: str | None) -> str | None:
    """Best-effort: 'flux'/'sdxl'/'sd15'/'wan' from the model id."""
    if not model_choice:
        return None
    s = model_choice.lower()
    if "flux" in s:
        return "flux"
    if "sdxl" in s:
        return "sdxl"
    if "sd15" in s or "sd_15" in s or "sd 1.5" in s:
        return "sd15"
    if "wan" in s:
        return "wan"
    return None


def _local_pick_loras(
    prompt: str, catalog: ImageCatalog, *,
    max_loras: int, model_choice: str | None,
) -> list[dict]:
    """Token-Jaccard match between prompt and each LoRA's
    {alias, trigger_words, category} string. Top-K by score, with a
    pipeline filter when we can infer the active model's pipeline."""
    if max_loras <= 0:
        return []
    prompt_tokens = {
        t.lower() for t in _TOKEN_RE.findall(prompt or "")
        if len(t) > 2 and t.lower() not in _PROMPT_STOP
    }
    if not prompt_tokens:
        return []
    target_pipeline = _model_pipeline(model_choice)
    scored: list[tuple[float, "LoraEntry"]] = []
    for lora in catalog.loras:
        if target_pipeline and lora.pipeline.lower() != target_pipeline:
            continue
        bag = " ".join((
            lora.alias or "", lora.trigger_words or "", lora.category or "",
        )).lower()
        bag_tokens = {
            t for t in _TOKEN_RE.findall(bag)
            if len(t) > 2 and t not in _PROMPT_STOP
        }
        if not bag_tokens:
            continue
        overlap = len(prompt_tokens & bag_tokens)
        if overlap == 0:
            continue
        score = overlap / max(len(bag_tokens | prompt_tokens), 1)
        scored.append((score, lora))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[dict] = []
    for _score, lora in scored[:max_loras]:
        display = (
            f"{lora.alias} ({lora.pipeline.upper()}) [trigger: {lora.trigger_words}]"
            if lora.trigger_words
            else f"{lora.alias} ({lora.pipeline.upper()})"
        )
        out.append({"choice": display, "strength": lora.default_strength})
    if out:
        log.info(
            "local lora picker: %d hits for prompt=%r, picks=%s",
            len(out), (prompt or "")[:60],
            [p["choice"][:40] for p in out],
        )
    return out


def resolve_lora_aliases(
    aliases: list[str],
    catalog: ImageCatalog,
) -> list[dict]:
    """Turn ["Real Beauty", "Lighting Slider"] into
    [{"choice": "...", "strength": <default>}] using the catalog's strengths.

    Unknown aliases are dropped (logged).
    """
    out: list[dict] = []
    by_alias = {lora.alias.lower(): lora for lora in catalog.loras}
    for raw in aliases:
        a = raw.strip().lower()
        if not a:
            continue
        lora = by_alias.get(a)
        if lora is None:
            log.warning("unknown lora alias: %s", raw)
            continue
        # Mirror _lora_to_choice_string format from ai_generate.py
        display = (
            f"{lora.alias} ({lora.pipeline.upper()}) [trigger: {lora.trigger_words}]"
            if lora.trigger_words
            else f"{lora.alias} ({lora.pipeline.upper()})"
        )
        out.append({"choice": display, "strength": lora.default_strength})
    return out


def compact_catalog_block(
    catalog: ImageCatalog,
    *,
    max_chars: int = 1500,
    include_nsfw: bool = False,
) -> str:
    """Format the catalog as a short system-prompt-friendly block.

    Compact by design — Terry doesn't need the full registry dump. If the
    catalog is bigger than `max_chars`, we drop the trigger-word strings
    first (they're often the longest), then start trimming LoRAs.
    """
    if not catalog.loaded or (not catalog.loras and not catalog.presets):
        return ""

    def _lora_line(lora: LoraEntry, with_trigger: bool) -> str:
        base = f"- {lora.alias} ({lora.pipeline})"
        if with_trigger and lora.trigger_words:
            base += f" — trigger: {lora.trigger_words}"
        elif lora.category:
            base += f" — {lora.category}"
        return base

    safe_loras = [l for l in catalog.loras if include_nsfw or not l.nsfw]
    safe_presets = [p for p in catalog.presets if include_nsfw or not p.nsfw]

    aspects_line = "Aspects: " + ", ".join(
        f"{k} ({w}x{h})" for k, (w, h) in ASPECT_RATIOS.items()
    )
    presets_line = "Presets: " + ", ".join(p.name for p in safe_presets) \
        if safe_presets else ""

    header = (
        "--- BEGIN IMAGE CATALOG ---\n"
        "When asked for a picture, emit:\n"
        "  [GENERATE_IMAGE] <plain prompt>                              # auto-LoRA + default size\n"
        "  [GENERATE_IMAGE] {\"prompt\":\"...\",\"aspect\":\"portrait\",\"loras\":[\"Real Beauty\"],"
        "\"preset\":null,\"negative\":\"\",\"count\":1}\n"
        "Rules: JSON on one line after the marker, no markdown fences. "
        "Leave loras empty ([]) to skip auto-pick. Aspect and preset are optional.\n"
    )
    footer = "--- END IMAGE CATALOG ---"

    # First try with trigger words
    for with_trigger in (True, False):
        lora_lines = [_lora_line(l, with_trigger) for l in safe_loras]
        block = (
            header
            + aspects_line + "\n"
            + (presets_line + "\n" if presets_line else "")
            + "LoRAs available:\n"
            + "\n".join(lora_lines) + "\n"
            + footer
        )
        if len(block) <= max_chars:
            return block

    # Still too big — trim LoRAs from the end until it fits.
    lora_lines = [_lora_line(l, False) for l in safe_loras]
    while lora_lines:
        block = (
            header
            + aspects_line + "\n"
            + (presets_line + "\n" if presets_line else "")
            + "LoRAs available:\n"
            + "\n".join(lora_lines) + "\n"
            + "(catalog truncated — ask for a specific LoRA by alias)\n"
            + footer
        )
        if len(block) <= max_chars:
            return block
        lora_lines.pop()

    # No LoRAs fit — minimal block.
    return header + aspects_line + "\n" + (presets_line + "\n" if presets_line else "") + footer


def pointer_catalog_block(catalog: ImageCatalog) -> str:
    """Compact pointer Terry sees in her system prompt.

    Replaces the full LoRA dump (saves ~1500 chars). Tells Terry that
    detailed LoRA info lives in the vault and how to reach it. The chat
    route also runs a proactive vault search on image-request turns, so
    Terry usually doesn't need to look up the catalog manually.
    """
    if not catalog.loaded and not catalog.loras and not catalog.presets:
        return ""

    safe_loras = [l for l in catalog.loras if not l.nsfw]
    safe_presets = [p for p in catalog.presets if not p.nsfw]
    pipelines = sorted({l.pipeline for l in safe_loras if l.pipeline})

    lines = [
        "--- BEGIN IMAGE CATALOG (POINTER) ---",
        "When asked for a picture, emit:",
        "  [GENERATE_IMAGE] <plain prompt>                              # auto-LoRA + default size",
        "  [GENERATE_IMAGE] {\"prompt\":\"...\",\"aspect\":\"portrait\","
        "\"loras\":[\"<alias>\"],\"preset\":null,\"negative\":\"\",\"count\":1}",
        "Do NOT dump catalog content into chat. If you need the full LoRA list,",
        "search the vault: knowledge/imagegen-loras.md. The gateway also pulls",
        "relevant vault notes for you automatically on image-request turns.",
        "Aspects: " + ", ".join(
            f"{k} ({w}x{h})" for k, (w, h) in ASPECT_RATIOS.items()
        ),
    ]
    if safe_presets:
        lines.append("Presets: " + ", ".join(p.name for p in safe_presets[:12])
                     + (", …" if len(safe_presets) > 12 else ""))
    lines.append(
        f"Pipelines available: {', '.join(pipelines) or '—'}. "
        f"FLUX = natural-language prompts, no negatives. SDXL/Pony = tag prompts + negatives."
    )
    lines.append(f"Total LoRAs available: {len(safe_loras)} (NSFW filtered).")
    lines.append("--- END IMAGE CATALOG (POINTER) ---")
    return "\n".join(lines)


def catalog_as_json(catalog: ImageCatalog, *, include_nsfw: bool = False) -> dict:
    """Serialize catalog for the /v1/images/catalog REST endpoint."""
    return {
        "loras": [
            {
                "alias": l.alias,
                "pipeline": l.pipeline,
                "trigger_words": l.trigger_words,
                "default_strength": l.default_strength,
                "category": l.category,
                "nsfw": l.nsfw,
            }
            for l in catalog.loras
            if include_nsfw or not l.nsfw
        ],
        "aspects": {k: {"width": w, "height": h} for k, (w, h) in ASPECT_RATIOS.items()},
        "presets": [
            {
                "name": p.name,
                "category": p.category,
                "width": p.width,
                "height": p.height,
                "steps": p.steps,
                "guidance": p.guidance,
                "negative": p.negative,
                "loras": p.loras,
                "tags": p.tags,
                "nsfw": p.nsfw,
            }
            for p in catalog.presets
            if include_nsfw or not p.nsfw
        ],
    }
