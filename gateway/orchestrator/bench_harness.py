"""Run a list of bench cases against a runtime invoker, score, return
the aggregate BenchScore.

Used by the CLI ``python -m gateway.orchestrator.bench_harness``
(which iterates roles × candidates from the catalog) and also by
unit tests directly.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import statistics
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from gateway.model_catalog import ModelCatalog, ModelEntry, load_catalog
from gateway.orchestrator.bench_corpus import BenchCase, list_roles, load_corpus
from gateway.orchestrator.bench_results import (
    BenchResults,
    BenchScore,
    load_results,
    save_results,
)
from gateway.orchestrator.quality_scorer import score_output
from gateway.orchestrator.runtimes.claude_runtime import invoke_claude
from gateway.orchestrator.runtimes.ollama_runtime import BenchInvocation, invoke_ollama


Invoker = Callable[[BenchCase], Awaitable[BenchInvocation]]

_log = logging.getLogger("gateway.orchestrator.bench_harness")


async def bench_role_against_model(
    *,
    cases: list[BenchCase],
    invoker: Invoker,
    model_id: str,
    cost_per_1k_tokens: float,
) -> BenchScore:
    """Run every case through ``invoker``, aggregate latency / tokens / quality.

    - latency_p50_ms = median of per-case latencies.
    - tokens_per_s = total output tokens / total wall-clock seconds.
    - quality_score = mean of per-case quality_scorer outputs.

    ``model_id`` is taken for symmetry with the call site but isn't
    part of the returned score (the caller keys the score by it).
    """
    if not cases:
        raise ValueError("cases must be non-empty")

    invocations: list[BenchInvocation] = []
    qualities: list[float] = []

    for case in cases:
        inv = await invoker(case)
        invocations.append(inv)
        qualities.append(score_output(case, inv.output))

    latencies = [inv.latency_ms for inv in invocations]
    total_tokens = sum(inv.token_count for inv in invocations)
    total_seconds = sum(inv.latency_ms for inv in invocations) / 1000.0
    tokens_per_s = total_tokens / total_seconds if total_seconds > 0 else 0.0

    return BenchScore(
        latency_p50_ms=float(statistics.median(latencies)),
        tokens_per_s=float(tokens_per_s),
        quality_score=float(statistics.mean(qualities)),
        cost_per_1k_tokens=float(cost_per_1k_tokens),
        last_run_at=time.time(),
    )


def _build_invoker(
    *,
    model: ModelEntry,
    ollama_host_url: str,
    anthropic_api_key: str | None,
) -> Invoker | None:
    """Return an async invoker for ``model``, or None if it can't be served
    in this environment (e.g. cloud model without API key)."""
    if model.cloud_provider == "anthropic":
        if not anthropic_api_key:
            _log.warning(
                "skipping cloud candidate %r: ANTHROPIC_API_KEY not set",
                model.id,
            )
            return None
        target = model.cloud_model_name
        api_key = anthropic_api_key
        max_tokens_default = 256

        async def _claude_invoke(case: BenchCase) -> BenchInvocation:
            return await invoke_claude(
                api_key=api_key,
                model=target,
                prompt=case.prompt,
                max_tokens=case.max_tokens or max_tokens_default,
            )

        return _claude_invoke

    if model.cloud_provider is not None:
        _log.warning(
            "skipping candidate %r: unsupported cloud_provider %r",
            model.id,
            model.cloud_provider,
        )
        return None

    if not model.ollama_name:
        _log.warning("skipping candidate %r: no ollama_name", model.id)
        return None

    target = model.ollama_name
    host = ollama_host_url
    # Models registered with gpu_vram_mb=0 are CPU-only at runtime
    # (OllamaInvoker.chat sets options.num_gpu=0 when use_cpu is set on
    # the helper task — see helpers/base.py). The bench has to take the
    # same path or its latency numbers describe a GPU run that production
    # never sees.
    cpu_only = model.gpu_vram_mb == 0
    num_gpu = 0 if cpu_only else None

    async def _ollama_invoke(case: BenchCase) -> BenchInvocation:
        return await invoke_ollama(
            host_url=host,
            model=target,
            prompt=case.prompt,
            max_tokens=case.max_tokens,
            num_gpu=num_gpu,
        )

    return _ollama_invoke


async def run_full_sweep(
    *,
    catalog: ModelCatalog,
    corpus_dir: Path,
    results_path: Path,
    ollama_host_url: str,
    anthropic_api_key: str | None,
    only_role: str | None = None,
) -> BenchResults:
    """For every helper role with a corpus file, for every candidate
    model, run the bench and collect a BenchScore. Merges with any
    previously-persisted scores so partial sweeps don't wipe history.

    ``only_role`` restricts the sweep to one role; useful for the
    ``python -m gateway.orchestrator.bench_harness --role X`` CLI mode.
    """
    existing = load_results(results_path)
    roles = list_roles(corpus_dir)

    if only_role is not None:
        if only_role not in roles:
            raise ValueError(
                f"role {only_role!r} has no corpus at {corpus_dir}",
            )
        roles = [only_role]

    for role in roles:
        cases = load_corpus(corpus_dir=corpus_dir, role=role)
        for model in catalog.candidates_for_role(role):
            invoker = _build_invoker(
                model=model,
                ollama_host_url=ollama_host_url,
                anthropic_api_key=anthropic_api_key,
            )
            if invoker is None:
                continue
            try:
                score = await bench_role_against_model(
                    cases=cases,
                    invoker=invoker,
                    model_id=model.id,
                    cost_per_1k_tokens=model.cost_per_1k_tokens_output,
                )
            except Exception as exc:
                _log.warning(
                    "bench failed for role=%s model=%s: %s",
                    role,
                    model.id,
                    exc,
                )
                continue
            existing.scores.setdefault(role, {})[model.id] = score
            _log.info(
                "scored role=%s model=%s quality=%.3f latency_p50=%dms",
                role,
                model.id,
                score.quality_score,
                int(score.latency_p50_ms),
            )

    save_results(results_path, existing)
    return existing


def _main() -> int:
    """Entry point for ``python -m gateway.orchestrator.bench_harness``."""
    parser = argparse.ArgumentParser(
        prog="bench_harness",
        description="Run canonical-prompt benchmarks across helper roles.",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("config/model_catalog.yaml"),
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path("config/bench_corpus"),
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=Path("state/bench_results.json"),
    )
    parser.add_argument(
        "--ollama-host",
        default=os.environ.get("OLLAMA_HOST_URL", "http://localhost:11434"),
    )
    parser.add_argument(
        "--role",
        default=None,
        help="Restrict sweep to a single helper role (default: all).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    catalog = load_catalog(args.catalog)
    args.results.parent.mkdir(parents=True, exist_ok=True)

    asyncio.run(
        run_full_sweep(
            catalog=catalog,
            corpus_dir=args.corpus_dir,
            results_path=args.results,
            ollama_host_url=args.ollama_host,
            anthropic_api_key=api_key,
            only_role=args.role,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
