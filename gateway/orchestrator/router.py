"""Helper-role → model selection.

Read-only at request time. The Router is constructed once at gateway
startup from ``ModelCatalog`` + ``BenchResults`` and pinned to
``app_state``. A bench harness run produces a fresh ``BenchResults``;
the gateway swaps the Router atomically (Phase 1 does swap, not
in-place refresh).

Credential checking
-------------------
Cloud models require a provider API key to be usable. ``Router`` checks
for the expected env var at routing time and silently skips any candidate
whose creds are missing, so a role can degrade to a local model (or to
the YAML default) without crashing the gateway.

Currently supported providers and their env var:
  - ``anthropic``  → ``ANTHROPIC_API_KEY``

New providers: add an entry to ``_PROVIDER_KEY_ENV`` below.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from gateway.model_catalog import ModelCatalog, ModelEntry
from gateway.orchestrator.bench_results import BenchResults, BenchScore

log = logging.getLogger("gateway.orchestrator.router")

# Map cloud_provider name → env-var that must be non-empty for the model
# to be considered available.  Add new providers here.
_PROVIDER_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
}


def _has_creds(model: ModelEntry) -> bool:
    """Return True if the model's cloud credentials are present (or it is local)."""
    if model.cloud_provider is None:
        return True  # local/Ollama models need no API key
    env_var = _PROVIDER_KEY_ENV.get(model.cloud_provider)
    if env_var is None:
        # Unknown provider — be conservative: assume creds are present so
        # we don't silently suppress a legitimate model.
        return True
    key = os.environ.get(env_var, "").strip()
    return bool(key)


@dataclass(frozen=True)
class ModelChoice:
    """Result of a routing decision."""
    model: ModelEntry
    reason: str  # e.g. "score=0.78 (q=0.90 lat=500ms cost=0.0008/1k)"


class Router:
    """Select a model for a helper role using bench scores + policy weights.

    Policy: composite_score = QUALITY_W * quality
                            + LATENCY_W * min(LATENCY_ANCHOR/latency, 1.0)
                            + COST_W * min(COST_ANCHOR/cost, 1.0)

    Free models score 1.0 on the cost term. Tiebreaker: lower cost.

    If no candidate has bench data, falls back to the role's default
    ``model:`` field from the YAML.
    """

    QUALITY_W = 0.5
    LATENCY_W = 0.3
    COST_W = 0.2

    LATENCY_ANCHOR_MS = 500.0
    COST_ANCHOR = 0.001

    def __init__(self, *, catalog: ModelCatalog, results: BenchResults) -> None:
        self._catalog = catalog
        self._results = results

    def route_for(self, role: str) -> ModelChoice:
        candidates = self._catalog.candidates_for_role(role)
        per_role = self._results.scores.get(role, {})

        scored: list[tuple[float, ModelEntry, BenchScore]] = []
        for model in candidates:
            if not _has_creds(model):
                log.debug(
                    "router: skipping %r for role %r — missing cloud creds",
                    model.id, role,
                )
                continue
            score = per_role.get(model.id)
            if score is None:
                continue
            composite = self._composite(score)
            scored.append((composite, model, score))

        if not scored:
            default_id = self._catalog.helper(role).model
            default = self._catalog.model(default_id)
            return ModelChoice(
                model=default,
                reason="no-bench: fallback to YAML default",
            )

        scored.sort(
            key=lambda t: (-t[0], t[2].cost_per_1k_tokens),
        )
        composite, model, score = scored[0]
        return ModelChoice(
            model=model,
            reason=(
                f"score={composite:.3f} "
                f"(q={score.quality_score:.2f} "
                f"lat={score.latency_p50_ms:.0f}ms "
                f"cost={score.cost_per_1k_tokens:.4f}/1k)"
            ),
        )

    def _composite(self, score: BenchScore) -> float:
        quality = score.quality_score
        latency_norm = self.LATENCY_ANCHOR_MS / max(
            score.latency_p50_ms, 1.0,
        )
        if score.cost_per_1k_tokens <= 0.0:
            cost_norm = 1.0
        else:
            cost_norm = self.COST_ANCHOR / score.cost_per_1k_tokens
        return (
            self.QUALITY_W * quality
            + self.LATENCY_W * min(latency_norm, 1.0)
            + self.COST_W * min(cost_norm, 1.0)
        )
