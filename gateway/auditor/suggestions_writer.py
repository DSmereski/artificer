# gateway/auditor/suggestions_writer.py
"""Review-only self-improvement suggestions derived from auditor findings.

When the auditor's hourly run produces a cluster of similar findings,
this module composes a single suggestion note and writes it to the
vault under `ops/auditor-suggestions/`. The note describes what the
operator should investigate or tune — never applies anything.

Pairs with the existing groomer suggestions writer: same shape (write
to vault, tag for human review), same guarantee (nothing auto-changes
the running system). Wiring is in `audit_run.run_audit`.

Why review-only: the auditor finding schema carries no per-role data
(see `auditor/findings.py`), so a routing-weight or prompt-version
adjustment derived from a finding would be guessing about which helper
caused it. A human reviewer is still in the loop; we just save them
the scanning step.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Protocol

from gateway.auditor.findings import Finding

log = logging.getLogger("gateway.auditor.suggestions_writer")


# How many findings of a given kind in one audit window trigger a
# suggestion. Calibrated to be quiet during steady state and only fire
# on real drift. Security is severity-1 because a single
# security-flagged turn already warrants review.
_THRESHOLDS = {
    "hallucination": 3,
    "skill_gap": 2,
    "unhandled_request": 3,
    "repeat_question": 5,
    "security": 1,
    "composio_unconfirmed": 1,
}

_REMEDIATIONS = {
    "hallucination": (
        "Likely planner / synthesizer drift. Suggested investigation:\n"
        "1. Re-run `python -m gateway.orchestrator.bench_harness "
        "--role planner` and `--role synthesizer`.\n"
        "2. Check the `planner_prompt_version` stamped on recent "
        "TurnRecords (introduced in Phase 1.5) — a recent prompt edit "
        "may correlate."
    ),
    "skill_gap": (
        "Recurring requests fell outside helper coverage. Add or extend "
        "a helper via the `/hive-add-helper` skill, or expand the "
        "matching skill's prompt to cover the new intent."
    ),
    "unhandled_request": (
        "User asks produced no useful action. Review router weights for "
        "retrieval roles (librarian / researcher): they may need a "
        "re-bench against a corpus that covers these intents."
    ),
    "repeat_question": (
        "User repeating themselves usually means context loss. "
        "Inspect `conversation_memory` tier sizes and the summarizer's "
        "`MID_SUMMARY_RENDER_CHAR_CAP`. A degenerate summarizer run "
        "would explain it."
    ),
    "security": (
        "Security-flagged turn detected. Review immediately and tighten "
        "the critic helper or upstream input validation."
    ),
    "composio_unconfirmed": (
        "A `saas_call` ran without a prior critic delegation. Check "
        "the action-executor gate and confirm the critic is wired "
        "into the dispatch path."
    ),
}


class _VaultLike(Protocol):
    async def learn(self, **kwargs: Any) -> dict | None: ...


def compose_suggestion_sections(findings: list[Finding]) -> list[str]:
    """Return one markdown section per kind whose count meets its
    threshold. Empty list when nothing crosses."""
    counts = Counter(f.kind for f in findings)
    sections: list[str] = []
    for kind, threshold in _THRESHOLDS.items():
        count = counts.get(kind, 0)
        if count < threshold:
            continue
        remediation = _REMEDIATIONS.get(kind, "")
        sections.append(
            f"### {kind}: {count} (threshold {threshold})\n\n{remediation}"
        )
    return sections


def _body(window_label: str, sections: list[str]) -> str:
    header = (
        f"Auditor suggestions for window `{window_label}`.\n\n"
        "**Review-only.** Nothing is applied automatically — these "
        "are pointers for the next investigation pass."
    )
    return header + "\n\n" + "\n\n".join(sections)


async def write_suggestions(
    *,
    vault: _VaultLike,
    window_label: str,
    findings: list[Finding],
) -> str | None:
    """Compose + persist a single suggestion note. Returns the body
    when one was written, None when nothing crossed threshold.

    Best-effort: vault write failures are logged and swallowed so the
    audit run itself never fails over a suggestion-write hiccup.
    """
    sections = compose_suggestion_sections(findings)
    if not sections:
        return None
    body = _body(window_label, sections)
    try:
        await vault.learn(
            category="ops/auditor-suggestions",
            title=f"Auditor suggestions — {window_label}",
            body=body,
            author="auditor",
            audience=["claude-code", "owner"],
            tags=["auditor", "auditor-suggestion", "review-only"],
        )
    except Exception:  # noqa: BLE001
        log.warning(
            "failed to write auditor suggestions for %s",
            window_label, exc_info=True,
        )
    return body
