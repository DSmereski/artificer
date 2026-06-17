"""Tests for the canonical-prompt corpus loader."""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from gateway.orchestrator.bench_corpus import (
    BenchCase,
    load_corpus,
    list_roles,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_load_corpus_parses_jsonl(tmp_path: Path) -> None:
    p = tmp_path / "chat_recall.jsonl"
    _write_jsonl(p, [
        {"id": "cr1", "prompt": "what did we say about kraken",
         "expected_keywords": ["kraken"], "max_tokens": 200},
        {"id": "cr2", "prompt": "summarize last week's threads",
         "expected_keywords": ["thread"], "max_tokens": 400},
    ])

    cases = load_corpus(corpus_dir=tmp_path, role="chat_recall")

    assert len(cases) == 2
    assert cases[0].id == "cr1"
    assert cases[0].prompt.startswith("what did")
    assert cases[0].expected_keywords == ("kraken",)
    assert cases[0].max_tokens == 200


def test_load_corpus_missing_role_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_corpus(corpus_dir=tmp_path, role="no_such_role")


def test_list_roles_returns_filenames(tmp_path: Path) -> None:
    (tmp_path / "chat_recall.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "synthesizer.jsonl").write_text("", encoding="utf-8")
    assert sorted(list_roles(tmp_path)) == ["chat_recall", "synthesizer"]


def test_load_corpus_validates_required_fields(tmp_path: Path) -> None:
    p = tmp_path / "chat_recall.jsonl"
    _write_jsonl(p, [{"id": "x"}])  # missing 'prompt'
    with pytest.raises(ValueError, match="prompt"):
        load_corpus(corpus_dir=tmp_path, role="chat_recall")
