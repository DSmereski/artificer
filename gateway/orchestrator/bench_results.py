"""Persistent store for per-(role, model) benchmark scores.

Schema (JSON):
    {
      "scores": {
        "<role>": {
          "<model_id>": {
            "latency_p50_ms": float,
            "tokens_per_s": float,
            "quality_score": float (0..1),
            "cost_per_1k_tokens": float (USD),
            "last_run_at": float (unix epoch s)
          }
        }
      }
    }
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from shared.atomic_write import atomic_write_json


@dataclass(frozen=True)
class BenchScore:
    latency_p50_ms: float
    tokens_per_s: float
    quality_score: float
    cost_per_1k_tokens: float
    last_run_at: float


@dataclass
class BenchResults:
    scores: dict[str, dict[str, BenchScore]] = field(default_factory=dict)


def load_results(path: Path) -> BenchResults:
    if not path.is_file():
        return BenchResults()
    raw = json.loads(path.read_text(encoding="utf-8"))
    scores = {
        role: {
            model_id: BenchScore(**score_dict)
            for model_id, score_dict in per_role.items()
        }
        for role, per_role in raw.get("scores", {}).items()
    }
    return BenchResults(scores=scores)


def save_results(path: Path, results: BenchResults) -> None:
    payload = {
        "scores": {
            role: {
                model_id: asdict(score)
                for model_id, score in per_role.items()
            }
            for role, per_role in results.scores.items()
        },
    }
    atomic_write_json(path, payload)
