"""Canonical-prompt corpus loader for benchmarking helper roles.

Each role has a JSONL file at ``<corpus_dir>/<role>.jsonl``. Each line is
a ``BenchCase`` dict with required fields ``id``, ``prompt`` and optional
``expected_keywords``, ``max_tokens``. The loader validates every line
on read; downstream code can rely on the schema.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BenchCase:
    """One canonical prompt for one helper role."""
    id: str
    prompt: str
    expected_keywords: tuple[str, ...] = ()
    max_tokens: int = 256


def list_roles(corpus_dir: Path) -> list[str]:
    """Return sorted role names for which a corpus file exists."""
    return sorted(
        p.stem for p in corpus_dir.glob("*.jsonl")
        if p.is_file()
    )


def load_corpus(*, corpus_dir: Path, role: str) -> list[BenchCase]:
    """Load + validate the corpus for one role.

    Raises ``FileNotFoundError`` if the corpus file is missing,
    ``ValueError`` if any row fails schema validation.
    """
    path = corpus_dir / f"{role}.jsonl"
    if not path.is_file():
        raise FileNotFoundError(
            f"no corpus for role={role!r} at {path}",
        )
    cases: list[BenchCase] = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1,
    ):
        if not line.strip():
            continue
        row = json.loads(line)
        if "id" not in row or "prompt" not in row:
            raise ValueError(
                f"{path}:{lineno}: row missing required field "
                "'id' or 'prompt'",
            )
        cases.append(BenchCase(
            id=str(row["id"]),
            prompt=str(row["prompt"]),
            expected_keywords=tuple(row.get("expected_keywords", ())),
            max_tokens=int(row.get("max_tokens", 256)),
        ))
    return cases
