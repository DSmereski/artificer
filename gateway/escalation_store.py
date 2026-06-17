"""Read+ack view of `escalate_to_dev` notes.

Hive's `_escalate_to_dev` writes one markdown note per dev-flagged
issue under `<vault>/ops/escalations/`. They sit there until somebody
(typically Claude Code, but the user can do it from the app too) marks
them resolved.

This module is the read-side that closes the loop architecturally —
without it, the queue grew but nothing ever drained it.

Resolution model: resolved escalations are renamed in place to
`<title>.resolved.md` rather than deleted. Keeps history grep-able and
makes accidental resolves recoverable. The `list()` filter hides
`.resolved.md` files from the open queue.

Vault path is the source of truth — no separate DB.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from vault_writer.util import parse_frontmatter


log = logging.getLogger("gateway.escalation_store")


@dataclass
class Escalation:
    slug: str            # filename without .md, used as the route id
    path: Path
    title: str
    severity: str        # "low" | "medium" | "high"
    reported_at: str     # ISO 8601
    device_id: str
    summary: str
    context: str
    user_msg: str
    resolved: bool
    body: str = ""

    def to_json(self) -> dict:
        return {
            "slug": self.slug,
            "title": self.title,
            "severity": self.severity,
            "reported_at": self.reported_at,
            "device_id": self.device_id,
            "summary": self.summary,
            "context": self.context,
            "user_msg": self.user_msg,
            "resolved": self.resolved,
        }


class EscalationStore:
    """Glob-and-parse view of `vault/ops/escalations/*.md`. Cheap;
    rebuilds on every list() so hand-edits on disk are visible without
    a gateway restart."""

    def __init__(self, vault_path: Path) -> None:
        self._vault = vault_path
        self._dir = vault_path / "ops" / "escalations"

    # ---------------------------------------------------------------- read
    def _split_section(self, body: str, heading: str) -> str:
        # Body sections are written with `## <heading>\n<text>\n\n## ...`.
        # A simple split is enough; we don't need a full markdown parser.
        m = re.search(
            rf"^##\s+{re.escape(heading)}\s*\n(.+?)(?=\n##\s+|\Z)",
            body, re.MULTILINE | re.DOTALL,
        )
        return (m.group(1).strip() if m else "")

    def _read_one(self, path: Path) -> Escalation | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("escalation %s unreadable: %s", path, e)
            return None
        try:
            fm, body = parse_frontmatter(raw)
        except Exception:  # noqa: BLE001
            log.warning("escalation %s frontmatter parse failed", path)
            return None
        if not isinstance(fm, dict):
            return None

        # Tag-driven severity is the canonical signal; the body line is
        # cosmetic. Fall back to the body line only if tags are missing.
        sev = "medium"
        tags = fm.get("tags") or []
        if isinstance(tags, list):
            for t in tags:
                if isinstance(t, str) and t.lower() in ("low", "medium", "high"):
                    sev = t.lower()
                    break
        if sev == "medium":
            m = re.search(r"\*\*Severity:\*\*\s+(\w+)", body, re.IGNORECASE)
            if m and m.group(1).lower() in ("low", "medium", "high"):
                sev = m.group(1).lower()

        slug = path.stem  # filename without ".md"
        is_resolved = slug.endswith(".resolved")
        if is_resolved:
            slug = slug[: -len(".resolved")]

        title = str(fm.get("title") or slug)
        reported_at = str(
            fm.get("escalation_ts") or fm.get("created_at") or ""
        )
        device_id = str(fm.get("device_id") or "")

        summary = self._split_section(body, "Summary")
        context = self._split_section(body, "Context")
        user_msg = self._split_section(body, "User message (verbatim)")
        return Escalation(
            slug=slug, path=path, title=title,
            severity=sev, reported_at=reported_at,
            device_id=device_id,
            summary=summary, context=context, user_msg=user_msg,
            resolved=is_resolved, body=body,
        )

    def list(self, *, include_resolved: bool = False) -> list[Escalation]:
        if not self._dir.is_dir():
            return []
        out: list[Escalation] = []
        for p in self._dir.glob("*.md"):
            esc = self._read_one(p)
            if esc is None:
                continue
            if not include_resolved and esc.resolved:
                continue
            out.append(esc)
        # Newest first by reported_at; falls back to mtime if the field
        # is missing on a hand-written note.
        out.sort(
            key=lambda e: (e.reported_at or "", e.path.stat().st_mtime),
            reverse=True,
        )
        return out

    def get(self, slug: str, *, include_resolved: bool = True) -> Escalation | None:
        for include in (False, True):
            if not include and include_resolved is False:
                continue
            for esc in self.list(include_resolved=include):
                if esc.slug == slug:
                    return esc
            if not include_resolved:
                break
        return None

    def count_open(self) -> int:
        return len(self.list(include_resolved=False))

    # ---------------------------------------------------------------- mutate
    def resolve(self, slug: str) -> bool:
        """Rename the escalation file to `<slug>.resolved.md`. Idempotent
        (no-op if already resolved). Returns True on success, False if
        the escalation doesn't exist."""
        for esc in self.list(include_resolved=True):
            if esc.slug != slug:
                continue
            if esc.resolved:
                return True
            # Double-check the rename target is inside the dir — paranoia
            # against any future codepath letting a slug carry separators.
            target = esc.path.with_name(f"{esc.slug}.resolved.md")
            try:
                target.relative_to(self._dir)
            except ValueError:
                log.warning(
                    "escalation resolve refused; target outside dir: %s",
                    target,
                )
                return False
            try:
                esc.path.rename(target)
            except OSError as e:
                log.warning("escalation resolve failed: %s", e)
                return False
            return True
        return False

    def reopen(self, slug: str) -> bool:
        """Inverse of resolve(). Mostly for tests + accidental resolves."""
        for esc in self.list(include_resolved=True):
            if esc.slug != slug or not esc.resolved:
                continue
            target = esc.path.with_name(f"{esc.slug}.md")
            try:
                esc.path.rename(target)
            except OSError as e:
                log.warning("escalation reopen failed: %s", e)
                return False
            return True
        return False
