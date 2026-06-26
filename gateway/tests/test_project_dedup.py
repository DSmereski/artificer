"""Project-scanner de-duplication + project deletion.

Regression for the duplicate-slug bug: decompose registered a project under a
kebab slug (e.g. 'android-tetris-game') for a squashed directory name
('androidtetrisgame'), then the scanner re-derived a squashed slug from the same
directory and minted a SECOND, disabled, 0-task project. The scanner now dedups
by directory path so a dir already owned by another slug is never duplicated.
"""

from __future__ import annotations

from pathlib import Path

from gateway.crew_board.project_scanner import scan
from gateway.crew_board.store import CrewBoardStore, Project


def _git_dir(parent: Path, name: str) -> Path:
    d = parent / name
    (d / ".git").mkdir(parents=True)
    return d


def test_scanner_skips_path_already_owned_by_another_slug(tmp_path: Path) -> None:
    """A directory already registered under a kebab slug (with a backslash path)
    must NOT get a second squashed-slug project from the scanner."""
    store = CrewBoardStore(tmp_path / "s.db")
    d = _git_dir(tmp_path, "androidtetrisgame")
    # Register under the kebab slug with a BACKSLASH path, as decompose did.
    store.upsert_project(Project(
        slug="android-tetris-game",
        path=str(d).replace("/", "\\"),
        name="android-tetris-game",
        enabled=True, push_allowed=False, test_cmd=None,
    ))

    scan(store, root=tmp_path)

    slugs = {p.slug for p in store.list_projects()}
    assert "androidtetrisgame" not in slugs, "scanner minted a squashed-name twin"
    assert "android-tetris-game" in slugs, "canonical project was dropped"
    assert len(slugs) == 1


def test_scanner_registers_new_repo_once(tmp_path: Path) -> None:
    """A genuinely-new repo is registered exactly once under its slug."""
    store = CrewBoardStore(tmp_path / "s.db")
    _git_dir(tmp_path, "brand-new-thing")

    scan(store, root=tmp_path)

    slugs = [p.slug for p in store.list_projects()]
    assert slugs == ["brand-new-thing"]


def test_scanner_is_idempotent_across_runs(tmp_path: Path) -> None:
    """Running the scanner twice does not create duplicates."""
    store = CrewBoardStore(tmp_path / "s.db")
    _git_dir(tmp_path, "repo-a")
    _git_dir(tmp_path, "repo-b")

    scan(store, root=tmp_path)
    scan(store, root=tmp_path)

    slugs = sorted(p.slug for p in store.list_projects())
    assert slugs == ["repo-a", "repo-b"]


def test_delete_project_removes_row(tmp_path: Path) -> None:
    store = CrewBoardStore(tmp_path / "d.db")
    store.upsert_project(Project(
        slug="junk", path=str(tmp_path / "junk"), name="junk",
        enabled=False, push_allowed=False, test_cmd=None,
    ))
    assert store.get_project("junk") is not None
    assert store.delete_project("junk") is True
    assert store.get_project("junk") is None
    # Deleting a missing slug is a harmless no-op.
    assert store.delete_project("does-not-exist") is False
