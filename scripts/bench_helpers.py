"""bench_helpers.py — measure how many concurrent helpers Ollama can serve.

Two GPU regimes:

  --mode full     all GPUs available (CUDA_VISIBLE_DEVICES is left alone
                  or set to "0,1,2"). Use when the user is NOT gaming.
  --mode gaming   only GPUs 1+2 (CUDA_VISIBLE_DEVICES=1,2). Use when the
                  user might fire up Star Citizen on GPU 0.

For each N in the sweep we:
  1. fire N parallel `OllamaInvoker.chat()` calls (rotated across three
     realistic helper-shaped prompts so we don't get pure cache hits)
  2. sample `nvidia-smi --query-gpu=memory.used` every 500 ms during the
     run and record the per-GPU peak
  3. record total wall, p50/p95 per-call, error count
  4. write one CSV row + print a live update

Outputs:
  bench_helpers_<mode>_<utc>.csv  next to this script.

Quick mode (`--quick`) sweeps just [1, 3, 5] for a fast sanity check.

This is intentionally a small standalone script — no fixtures, no
conftest. It assumes the Ollama server is already running and the
target model is already pulled.

Usage from repo root:
  python scripts/bench_helpers.py --mode full --max 12 --reps 1
  python scripts/bench_helpers.py --mode gaming --quick
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make `gateway` importable when run from anywhere.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# CUDA_VISIBLE_DEVICES must be set BEFORE the helper module loads so any
# native libraries see it. We do that based on --mode below, then import.


_DEFAULT_SWEEP = [1, 2, 3, 4, 6, 8, 10, 12]
_QUICK_SWEEP = [1, 3, 5]
_DEFAULT_MODEL = "planner-qwen"

# Realistic helper-shaped prompts — each ~150 input tokens, expects a
# small JSON-ish reply, mirrors what planner/sysmon/coder actually look
# like. We rotate these so cache hits don't skew the numbers.
_PROMPTS = [
    (
        "You are a planner. Given the user message, list up to 3 helpers "
        "to dispatch as JSON: {\"delegations\": [{\"role\": ...}]}.",
        "User asks: 'How are my GPUs holding up tonight?'. "
        "Available helpers: sysmon, librarian, coder. Respond with JSON only.",
    ),
    (
        "You are a sysmon helper. Summarise the host status as a "
        "single-paragraph English sentence under 30 words.",
        "GPU 0 (RTX 4080 Ti): 67C, 11.2/24GB used, 84% util. "
        "GPU 1 (5060 Ti): 42C, 3.8/16GB. GPU 2 (5060 Ti): 39C, 0.2/16GB. "
        "Disks: C: 312/465GB free. RAM: 38/64GB used.",
    ),
    (
        "You are a coder helper. Given a Python snippet, identify the "
        "first bug and propose a one-line fix. Reply with JSON: "
        "{\"bug\": str, \"fix\": str}.",
        "def average(xs):\n    return sum(xs) / len(xs)\n# Crashes when xs is empty.",
    ),
]


def _set_cuda_env(mode: str) -> str:
    """Apply the GPU mask for this run and return the resulting visibility."""
    if mode == "full":
        # Clear any prior `--mode gaming` mask so the user actually gets
        # all GPUs back. Note: this only affects what *this* Python
        # process tells subprocesses; the running Ollama server already
        # has its own CUDA_VISIBLE_DEVICES inherited from its launcher
        # (scripts/start-ollama-tuned.cmd sets "1,2"). To benchmark with
        # GPU 0 included, restart Ollama with the full mask first.
        os.environ.pop("CUDA_VISIBLE_DEVICES", None)
        return os.environ.get("CUDA_VISIBLE_DEVICES", "<unset>")
    if mode == "gaming":
        os.environ["CUDA_VISIBLE_DEVICES"] = "1,2"
        return "1,2"
    raise ValueError(f"unknown mode: {mode}")


async def _vram_sampler(stop_event: asyncio.Event) -> dict[int, int]:
    """Sample `nvidia-smi` every 500ms while not stopped. Return per-GPU peak MB."""
    peaks: dict[int, int] = {}
    while not stop_event.is_set():
        try:
            out = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=index,memory.used",
                    "--format=csv,noheader,nounits",
                ],
                timeout=2.0,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for line in out.strip().splitlines():
                idx_s, mb_s = (p.strip() for p in line.split(","))
                idx, mb = int(idx_s), int(mb_s)
                if mb > peaks.get(idx, -1):
                    peaks[idx] = mb
        except Exception:
            # nvidia-smi flakes occasionally; skip the sample.
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=0.5)
        except asyncio.TimeoutError:
            pass
    return peaks


async def _one_call(invoker, model: str, idx: int) -> tuple[float, bool]:
    sys_prompt, user_prompt = _PROMPTS[idx % len(_PROMPTS)]
    t0 = time.monotonic()
    try:
        await invoker.chat(model=model, system=sys_prompt, user=user_prompt)
        return (time.monotonic() - t0) * 1000.0, False
    except Exception as e:
        print(f"  call #{idx} errored: {e!r}", file=sys.stderr)
        return (time.monotonic() - t0) * 1000.0, True


async def _run_one(n: int, model: str, base_url: str) -> dict:
    """Fire N parallel calls, return latency + VRAM stats."""
    from gateway.helpers.base import OllamaInvoker  # late import — env first

    invoker = OllamaInvoker(base_url=base_url, timeout=120.0)
    stop = asyncio.Event()
    sampler = asyncio.create_task(_vram_sampler(stop))
    t0 = time.monotonic()
    results = await asyncio.gather(*[_one_call(invoker, model, i) for i in range(n)])
    wall_ms = (time.monotonic() - t0) * 1000.0
    stop.set()
    peaks = await sampler

    latencies = [r[0] for r in results]
    errors = sum(1 for r in results if r[1])
    return {
        "n": n,
        "wall_ms": int(wall_ms),
        "p50_ms": int(statistics.median(latencies)),
        "p95_ms": int(_pct(latencies, 95)),
        "errors": errors,
        # Stringify the dict so it survives a CSV column.
        "vram_peak_per_gpu_mb": ";".join(
            f"{idx}:{mb}" for idx, mb in sorted(peaks.items())
        ) or "-",
    }


def _pct(xs: list[float], pct: int) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = int(round((pct / 100.0) * (len(s) - 1)))
    return s[k]


async def _amain(args: argparse.Namespace) -> int:
    visibility = _set_cuda_env(args.mode)
    sweep = _QUICK_SWEEP if args.quick else _DEFAULT_SWEEP
    sweep = [n for n in sweep if n <= args.max]

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = ROOT / f"bench_helpers_{args.mode}_{ts}.csv"
    cols = ["mode", "rep", "n", "wall_ms", "p50_ms", "p95_ms", "errors", "vram_peak_per_gpu_mb"]
    print(f"# bench_helpers mode={args.mode} CUDA_VISIBLE_DEVICES={visibility}")
    print(f"# sweep={sweep} reps={args.reps} model={args.model}")
    print(f"# writing CSV → {out_path.name}")
    print()
    print("  mode  rep   N  wall   p50   p95  err  vram_peak")
    print("  ----  ---  ---  -----  ----  ----  ---  ---------")

    rows: list[dict] = []
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for rep in range(1, args.reps + 1):
            for n in sweep:
                row = await _run_one(n, args.model, args.base_url)
                row.update({"mode": args.mode, "rep": rep})
                w.writerow({k: row[k] for k in cols})
                fh.flush()
                rows.append(row)
                print(
                    f"  {args.mode:>4}  {rep:>3}  {n:>3}  "
                    f"{row['wall_ms']:>5}  {row['p50_ms']:>4}  "
                    f"{row['p95_ms']:>4}  {row['errors']:>3}  "
                    f"{row['vram_peak_per_gpu_mb']}"
                )

    print()
    print(f"# done — {len(rows)} rows in {out_path.name}")
    if any(r["errors"] for r in rows):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["full", "gaming"], required=True)
    p.add_argument("--max", type=int, default=12, help="upper N for the sweep")
    p.add_argument("--reps", type=int, default=1)
    p.add_argument("--quick", action="store_true", help="sweep [1,3,5] only")
    p.add_argument("--model", default=_DEFAULT_MODEL)
    p.add_argument("--base-url", default="http://localhost:11434")
    args = p.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
