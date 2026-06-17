# gateway/auditor/findings_writer.py
"""Compose the per-hour audit markdown + per-finding escalations.

All writes go through ``VaultClient.learn`` — the daemon side handles
audience-clamping and atomic writes. Severe findings (HIGH) ALSO get
written to ``ops/escalations/`` so they surface in the existing
escalations queue.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Protocol

from gateway.auditor.findings import Finding, Severity

log = logging.getLogger("gateway.auditor.findings_writer")


_KIND_LABEL = {
    "hallucination": "Hallucinations",
    "repeat_question": "Repeat questions",
    "unhandled_request": "Unhandled requests",
    "security": "Security flags",
    "skill_gap": "Skill gaps",
}
_KIND_ORDER = (
    "hallucination", "repeat_question", "unhandled_request",
    "security", "skill_gap",
)


class _VaultLike(Protocol):
    async def learn(self, **kwargs: Any) -> dict | None: ...


def _audit_body(*, turns_scanned: int, findings: list[Finding]) -> str:
    counts = Counter(f.kind for f in findings)
    by_kind: dict[str, list[Finding]] = {k: [] for k in _KIND_ORDER}
    for f in findings:
        by_kind.setdefault(f.kind, []).append(f)

    lines: list[str] = []
    lines.append("## Coverage")
    lines.append(f"- Turns scanned: {turns_scanned}")
    lines.append("")
    lines.append("## Findings")
    for kind in _KIND_ORDER:
        label = _KIND_LABEL[kind]
        lines.append(f"### {label}: {counts.get(kind, 0)}")
        for f in by_kind.get(kind, []):
            lines.append(f.to_markdown_bullet())
        lines.append("")
    return "\n".join(lines)


def _escalation_body(f: Finding, *, window_label: str) -> str:
    return "\n".join([
        "## Summary",
        f.summary,
        "",
        "## Context",
        f"Auditor finding from window {window_label}.",
        f"Kind: {f.kind}",
        f"Severity: {f.severity.name.lower()}",
        f"Bot: {f.bot}",
        f"Turn id: {f.turn_id}",
        "",
        "## User message (verbatim)",
        f.detail,
    ])


async def write_audit(
    *,
    vault: _VaultLike,
    window_label: str,
    turns_scanned: int,
    findings: list[Finding],
) -> None:
    """Write the per-hour audit summary; escalate HIGH findings."""
    body = _audit_body(turns_scanned=turns_scanned, findings=findings)
    try:
        await vault.learn(
            category="ops/audits",
            title=window_label,
            body=body,
            author="auditor",
            audience=["claude-code", "owner"],
            tags=["audit", "auditor"],
        )
    except Exception:  # noqa: BLE001
        log.exception("audit summary write failed")

    for f in findings:
        if f.severity != Severity.HIGH:
            continue
        slug = f"audit-{window_label}-{f.kind}-{f.turn_id}"
        try:
            await vault.learn(
                category="ops/escalations",
                title=slug,
                body=_escalation_body(f, window_label=window_label),
                author="auditor",
                audience=["claude-code", "owner"],
                tags=["high", "auditor", f.kind],
            )
        except Exception:  # noqa: BLE001
            log.exception("audit escalation write failed for %s", slug)
