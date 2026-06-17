# vault_writer/groomer/suggestions_writer.py
"""Materialise Suggestion objects as filesystem-as-truth markdown.

Output layout:
  <vault>/ops/groomer/<kind>/<slug>.md
  <vault>/ops/groomer/_runs/<ISO-ts>.md   (per-run summary)

Mirrors gateway/escalation_store.py — filesystem is the source of
truth. The synthesizer can read these later via VaultClient if/when
we wire that path; for now the user inspects them directly. Never
auto-applied.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from shared.atomic_write import atomic_write_text

from vault_writer.groomer.suggestion import (
    KINDS,
    REGISTRY,
    Suggestion,
    label_for,
)

log = logging.getLogger("vault_writer.groomer.suggestions_writer")


@dataclass
class WriteResult:
    files_written: int = 0
    files_unchanged: int = 0
    paths_written: list[str] = field(default_factory=list)


def _suggestion_path(vault_path: Path, s: Suggestion) -> Path:
    return vault_path / "ops" / "groomer" / s.kind / f"{s.slug}.md"


def _yaml_quote(value: str) -> str:
    """Wrap a string in YAML double-quotes, escaping backslashes and quotes."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_refs(refs: tuple[str, ...]) -> str:
    """Render the refs field as a YAML block sequence or empty inline list."""
    if not refs:
        return "refs: []"
    lines = ["refs:"]
    for ref in refs:
        lines.append(f"  - {_yaml_quote(ref)}")
    return "\n".join(lines)


def _render_suggestion(s: Suggestion, *, detected_at: str | None = None) -> str:
    """Render a Suggestion as YAML-frontmatter markdown.

    `detected_at` is rendered when supplied. Callers preserve a prior
    file's value when the proposal body is otherwise unchanged so the
    timestamp records first-detection, not last-write."""
    detected_line = f"detected_at: {detected_at}\n" if detected_at else ""
    return (
        "---\n"
        f"scanner: {s.kind}\n"
        f"confidence: {s.confidence:.3f}\n"
        f"{detected_line}"
        f"{_render_refs(s.refs)}\n"
        "---\n"
        f"# {s.title}\n\n"
        f"{s.body_md}"
    )


_DETECTED_RE = re.compile(r"^detected_at:\s*(.+)$", re.MULTILINE)


def _existing_detected_at(path: Path) -> str | None:
    """Read a prior detected_at value from an existing suggestion file, if any."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    m = _DETECTED_RE.search(text[4:end])
    return m.group(1).strip() if m else None


def _render_run_summary(
    *, now_ts: float, counts_by_kind: dict[str, int],
) -> str:
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts))
    body = [f"# Groomer run: {iso}", "", "## Coverage"]
    for kind in KINDS:
        body.append(f"- {label_for(kind)}: {counts_by_kind.get(kind, 0)}")
    body.append("")
    return "\n".join(body)


def write_suggestions(
    *,
    vault_path: Path,
    suggestions: Iterable[Suggestion],
    now_ts: float | None = None,
    counts_by_kind: dict[str, int] | None = None,
) -> WriteResult:
    res = WriteResult()
    if now_ts is None:
        now_ts = time.time()
    counts = counts_by_kind or {}

    iso_now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts))
    for s in suggestions:
        path = _suggestion_path(vault_path, s)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Preserve original detected_at across re-emissions of the same
        # proposal so the timestamp records first-detection, not last-write.
        prior_detected_at = _existing_detected_at(path) if path.exists() else None
        detected_at = prior_detected_at or iso_now
        new_body = _render_suggestion(s, detected_at=detected_at)
        if path.exists():
            try:
                if path.read_text(encoding="utf-8") == new_body:
                    res.files_unchanged += 1
                    continue
            except OSError:
                pass
        try:
            atomic_write_text(path, new_body)
            res.files_written += 1
            res.paths_written.append(str(path.relative_to(vault_path)).replace("\\", "/"))
        except OSError as e:
            log.warning("suggestion write failed: %s — %s", path, e)

    runs_dir = vault_path / "ops" / "groomer" / "_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    iso = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime(now_ts))
    run_path = runs_dir / f"{iso}.md"
    try:
        atomic_write_text(run_path, _render_run_summary(
            now_ts=now_ts, counts_by_kind=counts,
        ))
    except OSError as e:
        log.warning("run-summary write failed: %s — %s", run_path, e)

    return res
