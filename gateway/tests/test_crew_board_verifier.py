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
