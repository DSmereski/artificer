"""Unit tests for git worktree helpers (P6 parallel lanes).

Each test builds a throwaway git repo in tmp_path so worktree
add/remove is exercised for real. Skipped if git is unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from gateway.crew_board import worktree

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t.t")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "init")
    return r


def test_worktree_path_inside_repo(repo: Path) -> None:
    wt = worktree.worktree_path(repo, "T-0007")
    assert wt == repo / ".crew-worktrees" / "t-0007"


def test_ensure_creates_isolated_worktree(repo: Path) -> None:
    wt = worktree.ensure_worktree(repo, "T-0007")
    assert (wt / ".git").exists()
    assert (wt / "a.txt").read_text(encoding="utf-8") == "hello\n"
    # Branch crew/t-0007 now exists on the repo.
    out = subprocess.run(
        ["git", "branch", "--list", "crew/t-0007"],
        cwd=str(repo), capture_output=True, text=True,
    ).stdout
    assert "crew/t-0007" in out


def test_ensure_is_idempotent(repo: Path) -> None:
    a = worktree.ensure_worktree(repo, "T-1")
    b = worktree.ensure_worktree(repo, "T-1")
    assert a == b and (b / ".git").exists()


def test_edits_are_isolated_from_main_checkout(repo: Path) -> None:
    wt = worktree.ensure_worktree(repo, "T-2")
    (wt / "new.txt").write_text("x\n", encoding="utf-8")
    # Main checkout is untouched.
    assert not (repo / "new.txt").exists()


def test_remove_worktree_keeps_branch(repo: Path) -> None:
    worktree.ensure_worktree(repo, "T-3")
    worktree.remove_worktree(repo, "T-3")
    assert not worktree.worktree_path(repo, "T-3").exists()
    out = subprocess.run(
        ["git", "branch", "--list", "crew/t-3"],
        cwd=str(repo), capture_output=True, text=True,
    ).stdout
    assert "crew/t-3" in out  # branch survives for merge


def test_remove_missing_worktree_is_noop(repo: Path) -> None:
    worktree.remove_worktree(repo, "never-made")  # must not raise


def test_merge_into_base_lands_work(repo: Path) -> None:
    wt = worktree.ensure_worktree(repo, "T-9")
    (wt / "feature.py").write_text("x = 1\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "feat")
    # Before merge the base checkout doesn't have it.
    assert not (repo / "feature.py").exists()
    assert worktree.merge_into_base(repo, "T-9") is True
    # After merge the base checkout has the file.
    assert (repo / "feature.py").read_text(encoding="utf-8") == "x = 1\n"


def test_merge_into_base_missing_branch_returns_false(repo: Path) -> None:
    assert worktree.merge_into_base(repo, "never-made") is False


def test_unsafe_slug_rejected(repo: Path) -> None:
    import pytest as _pytest
    for bad in ("../escape", "T-1/../../x", "refs/heads/main", "a b"):
        with _pytest.raises(worktree.WorktreeError):
            worktree.worktree_path(repo, bad)
        with _pytest.raises(worktree.WorktreeError):
            worktree.branch_name(bad)
