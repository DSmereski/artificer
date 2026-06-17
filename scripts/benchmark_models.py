"""Multi-Model Coding Benchmark — sweep N candidate Ollama models
through the existing tier ladder (tiers 1-6) and write a single
master JSON report.

Per model:
  1. ollama pull <tag>            (idempotent; skipped if cached)
  2. probe /api/ps to record current VRAM allocation
  3. for each tier (1..max-tiers), run the hive agent loop
       - stops at first failure for that model
       - each tier gets its own project dir under
         C:/tmp/ai-team/bench/<model_slug>/tier<N>/
       - per-tier transcript: same dir, transcript.json
  4. before swapping to the next model, force eviction by calling
     /api/chat with keep_alive=0 (no-op prompt)

Output: C:/tmp/ai-team/bench/results.json with per-model x per-tier
metrics. The follow-up review session reads this file plus the
snapshot dirs to grade each model's code.

Run:
  python scripts/benchmark_models.py \
      --models qwen2.5-coder:7b,codegemma:7b \
      --max-tiers 2 \
      --max-iters 40
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Make the gateway package importable when run as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.spawn_tier_eval import TIERS, run_tier  # noqa: E402

BENCH_ROOT = Path(r"C:/tmp/ai-team/bench")
RESULTS_JSON = BENCH_ROOT / "results.json"
OLLAMA_BASE = "http://localhost:11434"

# Curated 12-model roster (matches the approved plan). Override via
# --models on the command line.
DEFAULT_MODELS = [
    # Use -instruct / chat-tuned variants — base coder models won't
    # follow the multi-step "write source, write test, run pytest,
    # call done" workflow reliably.
    "qwen2.5-coder:7b-instruct",
    "qwen2.5-coder:14b-instruct",
    "deepseek-coder-v2:16b",            # MoE; default tag is already instruct
    "codestral:22b",                    # already instruct
    "starcoder2:15b-instruct",
    "granite-code:8b-instruct",
    "yi-coder:9b-chat",
    "phi4:14b",                         # phi-4 is already instruct-tuned
    "codegemma:7b-instruct",
    "opencoder:8b-instruct",
    "qwen3-coder:30b-a3b-q3_K_M",
    "qwen3-coder:30b-a3b-q4_K_M",
]


def _slugify(tag: str) -> str:
    return (
        tag.replace(":", "-").replace("/", "-")
           .replace("_", "-").replace(".", "-").lower()
    )


def _ollama_show(tag: str) -> tuple[bool, str]:
    """Probe whether a tag is already cached or pullable. Uses
    /api/show; returns (exists_locally, err). When the model is not
    yet cached, /api/show returns 404 — that is normal; the pull
    step will fetch. We use this for tag-validation pre-flight by
    treating only network failures as fatal."""
    body = json.dumps({"name": tag}).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{OLLAMA_BASE}/api/show",
            data=body, headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        return True, ""
    except urllib.error.HTTPError as e:
        # 404 = not yet cached; let the pull handle it.
        if e.code == 404:
            return False, ""
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError) as e:
        return False, f"network: {e}"


def _preflight_summary(models: list[str]) -> dict:
    """Cheap pre-flight report: which models are already cached
    locally vs need to be pulled fresh. Doesn't validate tags exist
    on the registry — bad tags surface as pull failures later, which
    the per-model runner records and skips gracefully. Returns a
    summary dict for the printed header."""
    cached = []
    need_pull = []
    for m in models:
        ok, _ = _ollama_show(m)
        (cached if ok else need_pull).append(m)
    return {"cached": cached, "need_pull": need_pull}


def _ollama_pull(tag: str) -> tuple[bool, float, str]:
    """Pull model via the CLI. Idempotent — returns fast if cached.
    Returns (ok, duration_s, error_msg)."""
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            ["ollama", "pull", tag],
            capture_output=True,
            # Ollama spits ANSI progress bars in cp1252; decode loose
            # to avoid the harness blowing up on weird bytes.
            encoding="utf-8", errors="replace", timeout=3600,
        )
    except subprocess.TimeoutExpired:
        return False, time.monotonic() - t0, "pull timeout after 1hr"
    except (OSError, ValueError, UnicodeDecodeError) as e:
        return False, time.monotonic() - t0, f"spawn failed: {e}"
    if proc.returncode != 0:
        return False, time.monotonic() - t0, (proc.stderr or "")[-500:]
    return True, time.monotonic() - t0, ""


def _ollama_ps() -> list[dict]:
    """Return /api/ps payload — list of loaded models with VRAM info."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE}/api/ps", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data.get("models", []) or []
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return []


def _ollama_evict(tag: str) -> None:
    """Force-evict a model from VRAM by hitting /api/generate with an
    empty prompt + keep_alive=0. The server unloads the model on
    response. Best-effort — silently ignores errors."""
    body = json.dumps({
        "model": tag,
        "prompt": "",
        "keep_alive": 0,
        "stream": False,
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{OLLAMA_BASE}/api/generate",
            data=body, headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
    except (urllib.error.URLError, OSError):
        pass


def _snapshot_project(src: Path, dst: Path) -> None:
    """Copy the final project tree under bench/<model>/tier<N>/ so the
    review session can read each model's output."""
    import shutil
    dst.mkdir(parents=True, exist_ok=True)
    if not src.is_dir():
        return
    # Copy everything except .git + caches.
    for entry in src.iterdir():
        if entry.name in {".git", "__pycache__", ".pytest_cache"}:
            continue
        target = dst / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(
                                ".git", "__pycache__", ".pytest_cache",
                            ))
        else:
            shutil.copy2(entry, target)


def _load_results() -> dict:
    if RESULTS_JSON.is_file():
        try:
            return json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "framework_commit": _git_commit(),
        "models": {},
    }


def _save_results(results: dict) -> None:
    BENCH_ROOT.mkdir(parents=True, exist_ok=True)
    results["updated_at"] = datetime.now(timezone.utc).isoformat()
    RESULTS_JSON.write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8",
    )


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_REPO_ROOT), capture_output=True,
            encoding="utf-8", errors="replace", timeout=5,
        )
        return (out.stdout or "").strip() or "unknown"
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return "unknown"


async def _run_model_ladder(
    *, model: str, max_iters: int, max_tiers: int, results: dict,
) -> dict:
    """Run one model through up to `max_tiers` tiers. Returns the
    per-model entry that goes into results.json."""
    print(f"\n========== MODEL: {model} ==========")
    model_slug = _slugify(model)
    model_root = BENCH_ROOT / model_slug
    model_root.mkdir(parents=True, exist_ok=True)

    entry = results["models"].setdefault(model, {
        "model_slug": model_slug,
        "tiers": {},
        "highest_tier_cleared": 0,
        "total_dt_s": 0.0,
    })

    pull_ok, pull_dt, pull_err = _ollama_pull(model)
    entry["pull_ok"] = pull_ok
    entry["pull_dt_s"] = round(pull_dt, 1)
    if not pull_ok:
        entry["pull_error"] = pull_err
        print(f"[{model}] PULL FAILED: {pull_err}")
        _save_results(results)
        return entry

    # Record VRAM split right after the first chat call (lazy load).
    # We do that inside the first tier; here just record what's loaded.
    entry["ps_before"] = _ollama_ps()

    model_total_t0 = time.monotonic()
    for tier in sorted(TIERS, key=lambda t: t.num):
        if tier.num > max_tiers:
            break
        tier_dir = model_root / f"tier{tier.num}"
        transcript_path = tier_dir / "transcript.json"
        try:
            r = await run_tier(
                tier,
                max_iters=max_iters,
                model=model,
                transcript_path_override=transcript_path,
                project_dir_override=tier_dir / "workspace",
            )
        except Exception as e:  # noqa: BLE001
            print(f"[{model}] tier{tier.num} CRASHED: {e}")
            entry["tiers"][str(tier.num)] = {
                "error": f"{type(e).__name__}: {e}",
                "tier_pass": False,
            }
            _save_results(results)
            break

        snapshot_dir = tier_dir / "snapshot"
        _snapshot_project(tier_dir / "workspace", snapshot_dir)
        r["snapshot_path"] = str(snapshot_dir)
        entry["tiers"][str(tier.num)] = r

        if r["tier_pass"]:
            entry["highest_tier_cleared"] = max(
                entry["highest_tier_cleared"], tier.num,
            )
            # Capture VRAM allocation on the first successful tier
            # so we know how this model actually spread across GPUs.
            if "ps_during" not in entry:
                entry["ps_during"] = _ollama_ps()
            _save_results(results)
            print(f"[{model}] Tier {tier.num} PASS")
        else:
            _save_results(results)
            print(f"[{model}] Tier {tier.num} FAIL — stopping ladder")
            break

    entry["total_dt_s"] = round(time.monotonic() - model_total_t0, 1)
    _save_results(results)

    print(f"[{model}] evicting from VRAM...")
    _ollama_evict(model)

    return entry


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--models", default=",".join(DEFAULT_MODELS),
        help="Comma-separated Ollama model tags",
    )
    ap.add_argument("--max-tiers", type=int, default=6)
    ap.add_argument("--max-iters", type=int, default=60)
    ap.add_argument(
        "--skip-existing", action="store_true",
        help="Skip models that already have an entry in results.json",
    )
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        print("no models provided", file=sys.stderr)
        return 2

    import logging
    logging.basicConfig(level=logging.WARNING)

    results = _load_results()
    print(f"Bench root: {BENCH_ROOT}")
    print(f"Results: {RESULTS_JSON}")
    print(f"Models ({len(models)}): {models}")
    print(f"max-tiers={args.max_tiers}  max-iters={args.max_iters}")

    preflight = _preflight_summary(models)
    print(f"Pre-flight: {len(preflight['cached'])} cached, "
          f"{len(preflight['need_pull'])} need pull")
    if preflight["need_pull"]:
        print(f"  Will pull: {preflight['need_pull']}")

    for model in models:
        if args.skip_existing and results["models"].get(model, {}).get("tiers"):
            print(f"[{model}] already in results, skipping")
            continue
        try:
            await _run_model_ladder(
                model=model, max_iters=args.max_iters,
                max_tiers=args.max_tiers, results=results,
            )
        except Exception as e:  # noqa: BLE001
            # We've been losing the bench to silent crashes (ollama
            # connection drops, asyncio subprocess issues on Windows).
            # Log loud, record into results.json, keep climbing the
            # remaining models.
            import traceback
            tb = traceback.format_exc()
            print(f"\n[{model}] LADDER CRASH: {type(e).__name__}: {e}",
                  flush=True)
            print(tb, flush=True)
            results["models"].setdefault(model, {})["crash"] = {
                "type": type(e).__name__,
                "message": str(e),
                "traceback": tb[-3000:],
            }
            _save_results(results)

    print("\n========== BENCHMARK SUMMARY ==========")
    print(f"{'model':<45}  {'pulled':>7}  {'highest':>7}  {'total':>8}")
    for model, e in results["models"].items():
        highest = e.get("highest_tier_cleared", 0)
        total = e.get("total_dt_s", 0.0)
        pulled = "yes" if e.get("pull_ok") else "FAIL"
        print(f"{model:<45}  {pulled:>7}  {highest:>7}  {total:>7.1f}s")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except SystemExit:
        raise
    except BaseException as _e:  # noqa: BLE001
        import traceback as _tb
        print(f"\nFATAL: top-level {type(_e).__name__}: {_e}", flush=True)
        _tb.print_exc()
        raise SystemExit(1)
