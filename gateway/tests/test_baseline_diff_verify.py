"""Anti-stuck: baseline-diff verification + failing-test parser.

A chain must NOT freeze because the project already had a broken/flaky test.
The verifier captures a baseline of already-failing tests at chain start
(crew_meta `preflight:failing:<slug>`) and then passes a ticket as long as it
introduces NO NEW failures. With no baseline it stays strict (all-green), and a
non-parseable failure (compile error/crash) always fails strict.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gateway.crew_board import verifier
from gateway.crew_board.store import CrewBoardStore, Project
from gateway.crew_board.verifier import verify, _parse_failing_tests


@pytest.fixture()
def store(tmp_path: Path) -> CrewBoardStore:
    return CrewBoardStore(tmp_path / "crew_baseline.db")


def _project(store: CrewBoardStore, tmp_path: Path) -> str:
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir(exist_ok=True)
    store.upsert_project(Project(
        slug="bp", path=str(proj_dir), name="BP",
        enabled=True, push_allowed=False, test_cmd="flutter test",
    ))
    return "bp"


def _fake_run(failed_ids: list[str], exit_code: int = 1):
    return lambda p, **k: {
        "ran": True, "exit_code": exit_code, "reason": "",
        "stdout_tail": "", "stderr_tail": "", "failed_ids": failed_ids,
    }


def test_preexisting_failure_passes(store, tmp_path, monkeypatch):
    slug = _project(store, tmp_path)
    store.set_meta(f"preflight:failing:{slug}", json.dumps(["t.dart: old broken"]))
    monkeypatch.setattr(verifier, "_run_tests", _fake_run(["t.dart: old broken"]))
    task = store.create_task(title="t", body="b", project_slug=slug)
    result = verify(store, task)
    assert result.ok is True  # only the pre-existing failure → no NEW failures


def test_new_failure_fails(store, tmp_path, monkeypatch):
    slug = _project(store, tmp_path)
    store.set_meta(f"preflight:failing:{slug}", json.dumps(["t.dart: old broken"]))
    monkeypatch.setattr(
        verifier, "_run_tests",
        _fake_run(["t.dart: old broken", "n.dart: NEW regression"]),
    )
    task = store.create_task(title="t", body="b", project_slug=slug)
    result = verify(store, task)
    assert result.ok is False
    assert "NEW" in result.reason


def test_no_baseline_is_strict(store, tmp_path, monkeypatch):
    slug = _project(store, tmp_path)  # no baseline meta set
    monkeypatch.setattr(verifier, "_run_tests", _fake_run(["x.dart: fail"]))
    task = store.create_task(title="t", body="b", project_slug=slug)
    result = verify(store, task)
    assert result.ok is False  # strict all-green when no baseline exists


def test_unparseable_failure_fails_even_with_baseline(store, tmp_path, monkeypatch):
    """A non-zero exit with NO parseable failing ids (compile error / crash /
    timeout) must fail strict, never slip through baseline-diff."""
    slug = _project(store, tmp_path)
    store.set_meta(f"preflight:failing:{slug}", json.dumps(["t.dart: old broken"]))
    monkeypatch.setattr(verifier, "_run_tests", _fake_run([]))  # nothing parsed
    task = store.create_task(title="t", body="b", project_slug=slug)
    result = verify(store, task)
    assert result.ok is False


def test_green_suite_passes(store, tmp_path, monkeypatch):
    slug = _project(store, tmp_path)
    monkeypatch.setattr(verifier, "_run_tests", _fake_run([], exit_code=0))
    task = store.create_task(title="t", body="b", project_slug=slug)
    result = verify(store, task)
    assert result.ok is True


def test_parser_flutter():
    out = ("00:02 +186 -3: C:/p/test/theme/app_themes_test.dart: "
           "AppTheme exactly 8 themes [E]\n00:03 +200: All tests passed!")
    ids = _parse_failing_tests(out, "flutter test")
    assert ids == ["C:/p/test/theme/app_themes_test.dart: AppTheme exactly 8 themes"]


def test_parser_pytest():
    out = "FAILED tests/test_x.py::test_foo - assert 1 == 2\nPASSED tests/test_y.py"
    ids = _parse_failing_tests(out, "python -m pytest")
    assert ids == ["tests/test_x.py::test_foo"]
