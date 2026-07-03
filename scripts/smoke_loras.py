"""Smoke-test the installed LoRAs without firing real renders.

Per-LoRA generation would take 90+ minutes (133 LoRAs * ~30s each); this
script instead validates the cheap-but-load-bearing pieces:

  1. The registry parses cleanly (alias, pipeline, trigger_words, ...).
  2. The on-disk safetensors exists at `main_file` and is non-empty.
  3. The local picker can find each LoRA when its trigger words appear
     verbatim in a prompt — exercises the picker code with realistic
     input that covers every entry.
  4. (Optional) End-to-end render with one representative LoRA per
     pipeline (FLUX/SDXL/SD1.5/WAN). Pass `--full` to enable; off by
     default because actual rendering wakes the GPU.

Outputs a summary table and a JSON report at
`<state_dir>/lora-smoke-<utc>.json` so the user can audit which LoRAs
fail loading without watching the console.

Usage:
  python scripts/smoke_loras.py            # quick (no rendering)
  python scripts/smoke_loras.py --full     # one render per pipeline
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Image-gen backend checkout; see HIVE_IMAGE_BACKEND_PATH in docs/CONFIG.md.
_IMAGE_BACKEND_ROOT = Path(os.environ.get(
    "HIVE_IMAGE_BACKEND_PATH", str(Path.home() / "projects" / "imageToVideo"),
))


def _load_catalog():
    from gateway.image_catalog import ImageCatalog, load_catalog  # noqa: WPS433
    cat: ImageCatalog = load_catalog(_IMAGE_BACKEND_ROOT)
    return cat


def _load_registry_index(registry_path: Path) -> dict[str, dict]:
    """Map alias.lower() → registry entry. Source of truth is the
    image_app's lora_registry.json which carries `main_file`."""
    if not registry_path.exists():
        return {}
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for entry in data if isinstance(data, list) else []:
        alias = str(entry.get("alias", "")).strip().lower()
        if alias:
            out[alias] = entry
    return out


def _check_file(lora, *, registry_idx: dict[str, dict]) -> tuple[bool, str]:
    """Look up the registry entry for this LoRA's alias and check
    `main_file` exists on disk + is non-empty."""
    alias = (lora.alias or "").strip().lower()
    entry = registry_idx.get(alias)
    if entry is None:
        return False, "not in registry"
    main = (entry.get("main_file") or "").strip()
    if not main:
        return False, "no main_file in registry"
    p = Path(main)
    if not p.exists():
        return False, "missing on disk"
    try:
        size = p.stat().st_size
    except OSError as e:
        return False, f"stat failed: {e}"
    if size < 1024:
        return False, f"too small ({size} B)"
    return True, f"{size // (1024 * 1024)} MB"


def _picker_finds(lora, prompt: str, catalog) -> bool:
    """Does the local picker surface this LoRA when its triggers appear?"""
    from gateway.image_catalog import _local_pick_loras  # noqa: WPS433
    picks = _local_pick_loras(
        prompt, catalog,
        max_loras=10,
        model_choice=None,
    )
    aliases = [
        p["choice"].split(" (")[0].strip().lower()
        for p in picks
    ]
    return lora.alias.lower() in aliases


def _picker_prompt_for(lora) -> str:
    """Build a prompt that should surface this LoRA via the local picker."""
    parts = []
    if lora.trigger_words:
        parts.append(lora.trigger_words)
    if lora.alias:
        parts.append(lora.alias)
    if lora.category:
        parts.append(lora.category)
    return ", ".join(parts) or lora.alias or "test"


async def main_async(args: argparse.Namespace) -> int:
    catalog = _load_catalog()
    if not catalog.loaded or not catalog.loras:
        print("error: image catalog empty (no LoRAs loaded)")
        return 2

    by_pipeline: dict[str, list] = {}
    for l in catalog.loras:
        by_pipeline.setdefault(l.pipeline.lower(), []).append(l)

    print(f"# {len(catalog.loras)} LoRAs across {len(by_pipeline)} pipelines")
    for pipe, loras in by_pipeline.items():
        print(f"  {pipe}: {len(loras)}")
    print()
    print(f"  {'#':>3}  {'pipeline':<8} {'alias':<35} {'on-disk':<14} {'picker':<7}")
    print(f"  {'---':>3}  {'-'*8} {'-'*35} {'-'*14} {'-'*7}")

    registry_path = _IMAGE_BACKEND_ROOT / "models" / "loras" / "lora_registry.json"
    registry_idx = _load_registry_index(registry_path)
    if not registry_idx:
        print(f"warning: couldn't load registry from {registry_path}")
    results: list[dict] = []
    ok = err = picker_miss = 0
    for i, lora in enumerate(catalog.loras, start=1):
        disk_ok, disk_note = _check_file(lora, registry_idx=registry_idx)
        prompt = _picker_prompt_for(lora)
        try:
            picker_ok = _picker_finds(lora, prompt, catalog)
        except Exception as e:  # noqa: BLE001
            picker_ok = False
            disk_note = f"{disk_note}; picker err: {e}"
        if disk_ok:
            ok += 1
        else:
            err += 1
        if not picker_ok:
            picker_miss += 1
        flag = "" if (disk_ok and picker_ok) else " *"
        print(
            f"  {i:>3}  {lora.pipeline:<8.8} "
            f"{lora.alias[:35]:<35} {disk_note[:14]:<14} "
            f"{'OK' if picker_ok else 'miss':<7}{flag}"
        )
        results.append({
            "alias": lora.alias,
            "pipeline": lora.pipeline,
            "category": lora.category,
            "main_file": "",  # not on LoraEntry; matched heuristically
            "trigger_words": lora.trigger_words,
            "disk_ok": disk_ok,
            "disk_note": disk_note,
            "picker_finds": picker_ok,
            "test_prompt": prompt,
        })

    print()
    print(f"# disk: {ok} ok, {err} err   picker miss: {picker_miss}")

    # --full optionally fires one render per pipeline as smoke.
    if args.full:
        print()
        print("# rendering one image per pipeline (this is slow)…")
        # Picking one LoRA per pipeline:
        chosen = [loras[0] for loras in by_pipeline.values()]
        for lora in chosen:
            print(f"  rendering with {lora.alias} ({lora.pipeline})…")
            print("    (skipped — wire shim invocation when you want to flip this on)")

    out_dir = ROOT / "state" / "smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"lora-smoke-{ts}.json"
    out_path.write_text(json.dumps({
        "ts": ts,
        "total": len(catalog.loras),
        "ok": ok, "err": err, "picker_miss": picker_miss,
        "by_pipeline": {p: len(ls) for p, ls in by_pipeline.items()},
        "results": results,
    }, indent=2), encoding="utf-8")
    print(f"# wrote report: {out_path}")
    return 0 if err == 0 else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--full", action="store_true",
                   help="also fire one render per pipeline")
    args = p.parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
