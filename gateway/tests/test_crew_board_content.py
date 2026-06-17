"""Tests for content-request board tasks (kind='content')."""

from __future__ import annotations

from pathlib import Path

from gateway.crew_board.store import CrewBoardStore, Project


def _store(tmp_path: Path) -> CrewBoardStore:
    s = CrewBoardStore(tmp_path / "content.db")
    s.upsert_project(Project(slug="content", path="C:/Projects", name="Content",
                             enabled=True, push_allowed=False, test_cmd=None))
    return s


def test_content_task_roundtrips_kind_and_spec(tmp_path):
    s = _store(tmp_path)
    spec = {"type": "image", "prompt": "a copper dragon", "count": 2,
            "result_media_ids": []}
    t = s.create_task(title="image: a copper dragon", project_slug="content",
                      kind="content", content_spec=spec)
    got = s.get_task(t.slug)
    assert got.kind == "content"
    assert got.content_spec["prompt"] == "a copper dragon"
    assert got.content_spec["count"] == 2


def test_set_content_spec_persists_result_media(tmp_path):
    s = _store(tmp_path)
    t = s.create_task(title="image: x", project_slug="content",
                      kind="content", content_spec={"type": "image", "prompt": "x"})
    spec = dict(t.content_spec)
    spec["result_media_ids"] = ["abc123", "def456"]
    spec["state"] = "done"
    s.set_content_spec(t.slug, spec)
    got = s.get_task(t.slug)
    assert got.content_spec["result_media_ids"] == ["abc123", "def456"]
    assert got.content_spec["state"] == "done"


def test_default_code_kind_for_normal_tasks(tmp_path):
    s = _store(tmp_path)
    t = s.create_task(title="normal", project_slug="content")
    assert s.get_task(t.slug).kind == "code"
