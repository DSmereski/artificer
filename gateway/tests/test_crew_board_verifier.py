"""Tests for the verifier gate — especially the false-positive fix.

A CONFIGURED test_cmd that cannot run (spawn failure / missing project path)
must FAIL verification, not auto-pass. Only a genuinely-absent test_cmd is
permissive. Regression guard for the bug where `flutter test` failing to spawn
left tasks marked ok=true.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gateway.crew_board.store import CrewBoardStore, Project
from gateway.crew_board.verifier import verify


@pytest.fixture()
def store(tmp_path: Path) -> CrewBoardStore:
    return CrewBoardStore(tmp_path / "crew_verifier.db")


def _project(store: CrewBoardStore, tmp_path: Path, test_cmd: str | None) -> str:
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir(exist_ok=True)
    store.upsert_project(
        Project(
            slug="verproj",
            path=str(proj_dir),
            name="Ver Proj",
            enabled=True,
            push_allowed=False,
            test_cmd=test_cmd,
        )
    )
    return "verproj"


def _task(store: CrewBoardStore, slug: str = "verproj") -> Task:
    return store.create_task(title="t", body="b", project_slug=slug)


def test_unspawnable_test_cmd_fails_gate(store, tmp_path):
    """A configured test_cmd that can't spawn → ok False (not permissive)."""
    _project(store, tmp_path, "definitely_not_a_real_binary_zzz --run")
    task = _task(store)
    result = verify(store, task)
    assert result.ok is False
    assert "could not run" in result.reason


def test_absent_test_cmd_is_permissive(store, tmp_path):
    """No test_cmd configured → tests permissive; gate passes on files alone."""
    _project(store, tmp_path, None)
    task = _task(store)
    result = verify(store, task)
    assert result.ok is True


def test_passing_test_cmd_passes_gate(store, tmp_path):
    """A test_cmd that exits 0 → ok True."""
    _project(store, tmp_path, f'"{sys.executable}" -c "import sys; sys.exit(0)"')
    task = _task(store)
    result = verify(store, task)
    assert result.ok is True


def test_failing_test_cmd_fails_gate(store, tmp_path):
    """A test_cmd that exits non-zero → ok False."""
    _project(store, tmp_path, f'"{sys.executable}" -c "import sys; sys.exit(1)"')
    task = _task(store)
    result = verify(store, task)
    assert result.ok is False
    assert "tests failed" in result.reason


# --- #177 gates: boot (Flutter entry-point) + false-done (no committed work) ---

import subprocess as _sp


def _flutter_project(store, tmp_path, main_body: str | None):
    """A project dir with pubspec.yaml + optional lib/main.dart content."""
    proj = tmp_path / "fproj"
    (proj / "lib").mkdir(parents=True, exist_ok=True)
    (proj / "pubspec.yaml").write_text("name: app\n", encoding="utf-8")
    if main_body is not None:
        (proj / "lib" / "main.dart").write_text(main_body, encoding="utf-8")
    store.upsert_project(Project(slug="fproj", path=str(proj), name="F",
                                 enabled=True, push_allowed=False, test_cmd=None))
    return store.create_task(title="t", body="b", project_slug="fproj")


def test_flutter_no_main_fails_boot_gate(store, tmp_path):
    """Flutter lib/main.dart without main() → app can't launch → gate fails."""
    task = _flutter_project(store, tmp_path,
                            "import 'x';\nclass App extends StatelessWidget {}\n")
    result = verify(store, task)
    assert result.ok is False
    assert "main()" in result.reason


def test_flutter_missing_main_dart_fails(store, tmp_path):
    task = _flutter_project(store, tmp_path, None)  # no lib/main.dart at all
    result = verify(store, task)
    assert result.ok is False
    assert "main.dart" in result.reason


def test_flutter_with_main_passes_boot_gate(store, tmp_path):
    task = _flutter_project(store, tmp_path,
                            "void main() { runApp(const App()); }\n")
    result = verify(store, task)
    assert result.ok is True


def _git_project(store, tmp_path):
    proj = tmp_path / "gproj"
    proj.mkdir(exist_ok=True)
    def g(*a):
        _sp.run(["git", "-C", str(proj), *a], capture_output=True, text=True)
    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (proj / "seed.txt").write_text("seed\n", encoding="utf-8")
    g("add", "-A"); g("commit", "-q", "-m", "unrelated seed")
    store.upsert_project(Project(slug="gproj", path=str(proj), name="G",
                                 enabled=True, push_allowed=False, test_cmd=None))
    return proj, store.create_task(title="t", body="b", project_slug="gproj")


def test_false_done_clean_repo_no_task_commit_fails(store, tmp_path):
    """Clean tree + no commit referencing the task = nothing produced → fail."""
    _proj, task = _git_project(store, tmp_path)
    result = verify(store, task)
    assert result.ok is False
    assert "false-done" in result.reason


def test_dirty_tree_passes_commit_gate(store, tmp_path):
    """Uncommitted work present → commit gate is satisfied (loop commits after)."""
    proj, task = _git_project(store, tmp_path)
    (proj / "work.txt").write_text("did work\n", encoding="utf-8")  # uncommitted
    result = verify(store, task)
    assert result.ok is True
