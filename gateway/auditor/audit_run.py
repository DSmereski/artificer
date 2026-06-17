# gateway/auditor/audit_run.py
"""Auditor run orchestration: gather inputs → run scanners → write findings.

Pure function. The caller (scheduler / CLI / test) supplies the time
window and a VaultClient-shaped ``vault`` object; this module owns
loading turn-logs, loading per-bot memory, calling each scanner, and
delegating to ``findings_writer.write_audit``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

from gateway.auditor.findings import Finding
from gateway.auditor.findings_writer import write_audit
from gateway.auditor.inputs import load_thread_memories, load_turns_in_window
from gateway.auditor.suggestions_writer import write_suggestions
from gateway.auditor.scanners.base import Scanner
from gateway.auditor.scanners.composio_scanner import ComposioScanner
from gateway.auditor.scanners.hallucination import HallucinationScanner
from gateway.auditor.scanners.repeat_question import RepeatQuestionScanner
from gateway.auditor.scanners.security import SecurityScanner
from gateway.auditor.scanners.skill_gap import SkillGapScanner
from gateway.auditor.scanners.unhandled_request import UnhandledRequestScanner

log = logging.getLogger("gateway.auditor.audit_run")


class _VaultLike(Protocol):
    async def learn(self, **kwargs: Any) -> dict | None: ...


def default_scanners() -> list[Scanner]:
    return [
        RepeatQuestionScanner(),
        UnhandledRequestScanner(),
        SecurityScanner(),
        SkillGapScanner(),
        HallucinationScanner(),
        ComposioScanner(),
    ]


async def run_audit(
    *,
    state_dir: Path,
    vault: _VaultLike,
    bots: list[str],
    window_start: float,
    window_end: float,
    window_label: str,
    scanners: list[Scanner] | None = None,
) -> list[Finding]:
    """Run one audit pass over [window_start, window_end] for ``bots``.

    Returns the list of findings (also written to vault).
    """
    log_root = state_dir / "turn-logs"
    memory_root = state_dir / "memory"

    turns = load_turns_in_window(
        log_root=log_root,
        window_start=window_start,
        window_end=window_end,
    )
    memories: list[dict] = []
    for bot in bots:
        memories.extend(load_thread_memories(memory_root=memory_root, bot=bot))

    scs = scanners if scanners is not None else default_scanners()
    findings: list[Finding] = []
    for scanner in scs:
        try:
            findings.extend(scanner.scan(turns=turns, memories=memories))
        except Exception:  # noqa: BLE001
            log.exception("scanner %s failed", getattr(scanner, "name", "?"))

    await write_audit(
        vault=vault,
        window_label=window_label,
        turns_scanned=len(turns),
        findings=findings,
    )
    # Phase 3.2: emit a review-only suggestion note when a finding
    # kind clusters past its threshold in this window. The note goes
    # to ops/auditor-suggestions/; nothing is applied automatically.
    await write_suggestions(
        vault=vault,
        window_label=window_label,
        findings=findings,
    )
    return findings
