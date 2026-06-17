"""Tests for `gateway.escalation_store.EscalationStore`.

These pin the read-side that closes the loop on `escalate_to_dev`. The
contract under test:

  - list() globs `vault/ops/escalations/*.md`, parses frontmatter +
    body sections.
  - resolved entries (`*.resolved.md`) are hidden by default and surfaced
    by include_resolved=True.
  - resolve(slug) renames the file in place; idempotent.
  - reopen(slug) is the inverse for accidental resolves.

We hand-write the markdown so the test doesn't depend on the writer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.escalation_store import EscalationStore


def _write(vault: Path, name: str, *, severity="medium",
           ts="2026-04-29T17:00:00Z", title="bad bug",
           summary="things broke", context="things broke harder",
           user_msg="why is it broken") -> Path:
    d = vault / "ops" / "escalations"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(
        f"---\n"
        f"title: {title}\n"
        f"escalation_ts: {ts}\n"
        f"device_id: dev-abc\n"
        f"tags: [escalation, {severity}]\n"
        f"audience: [claude-code]\n"
        f"---\n\n"
        f"**Severity:** {severity}\n"
        f"**Reported at:** {ts}\n"
        f"**Device:** `dev-abc`\n\n"
        f"## Summary\n{summary}\n\n"
        f"## Context\n{context}\n\n"
        f"## User message (verbatim)\n{user_msg}\n",
        encoding="utf-8",
    )
    return p


def test_list_empty_when_dir_missing(tmp_path):
    s = EscalationStore(tmp_path)
    assert s.list() == []
    assert s.count_open() == 0


def test_list_parses_frontmatter_and_sections(tmp_path):
    _write(tmp_path, "esc-001.md", severity="high",
           summary="image render hangs",
           context="repro: send 5 wallpaper requests in a row, 5th hangs forever")
    s = EscalationStore(tmp_path)
    items = s.list()
    assert len(items) == 1
    e = items[0]
    assert e.slug == "esc-001"
    assert e.severity == "high"
    assert e.summary == "image render hangs"
    assert "repro: send 5 wallpaper" in e.context
    assert e.user_msg == "why is it broken"
    assert e.resolved is False


def test_list_hides_resolved_by_default(tmp_path):
    _write(tmp_path, "esc-A.md")
    _write(tmp_path, "esc-B.resolved.md", title="already done")
    s = EscalationStore(tmp_path)
    open_only = s.list()
    assert len(open_only) == 1
    assert open_only[0].slug == "esc-A"
    assert s.count_open() == 1
    all_items = s.list(include_resolved=True)
    assert {e.slug for e in all_items} == {"esc-A", "esc-B"}
    assert any(e.resolved for e in all_items)


def test_list_sorts_newest_first_by_reported_at(tmp_path):
    _write(tmp_path, "esc-old.md", ts="2026-04-01T00:00:00Z")
    _write(tmp_path, "esc-new.md", ts="2026-04-29T00:00:00Z")
    s = EscalationStore(tmp_path)
    slugs = [e.slug for e in s.list()]
    assert slugs == ["esc-new", "esc-old"]


def test_resolve_renames_in_place(tmp_path):
    _write(tmp_path, "esc-fixme.md")
    s = EscalationStore(tmp_path)
    assert s.count_open() == 1
    assert s.resolve("esc-fixme") is True
    assert s.count_open() == 0
    # The .md file got renamed; the .resolved.md sibling exists.
    assert not (tmp_path / "ops/escalations/esc-fixme.md").exists()
    assert (tmp_path / "ops/escalations/esc-fixme.resolved.md").exists()
    # Idempotent.
    assert s.resolve("esc-fixme") is True


def test_resolve_unknown_slug_returns_false(tmp_path):
    (tmp_path / "ops" / "escalations").mkdir(parents=True)
    s = EscalationStore(tmp_path)
    assert s.resolve("nope") is False


def test_reopen_undoes_resolve(tmp_path):
    _write(tmp_path, "esc-X.md")
    s = EscalationStore(tmp_path)
    assert s.resolve("esc-X")
    assert s.count_open() == 0
    assert s.reopen("esc-X")
    assert s.count_open() == 1


def test_severity_falls_back_to_body_when_tags_missing(tmp_path):
    """A hand-edited note (no `tags:` field) still parses severity from
    the body line so it doesn't quietly default to 'medium'."""
    d = tmp_path / "ops" / "escalations"
    d.mkdir(parents=True)
    (d / "esc-old.md").write_text(
        "---\n"
        "title: hand written\n"
        "escalation_ts: 2026-04-01T00:00:00Z\n"
        "device_id: legacy\n"
        "audience: [claude-code]\n"
        "---\n\n"
        "**Severity:** high\n\n"
        "## Summary\nold-style note\n",
        encoding="utf-8",
    )
    s = EscalationStore(tmp_path)
    [e] = s.list()
    assert e.severity == "high"


def test_get_finds_resolved(tmp_path):
    _write(tmp_path, "esc-Y.md")
    s = EscalationStore(tmp_path)
    s.resolve("esc-Y")
    e = s.get("esc-Y", include_resolved=True)
    assert e is not None
    assert e.resolved is True
