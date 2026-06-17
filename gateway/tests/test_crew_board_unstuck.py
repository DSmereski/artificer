"""Tests for the crew-board 'unstuck' Claude flow (pure parts).

We don't spawn the real `claude` CLI. We test the stack-detection helper and
the prompt guardrail that exist to prevent the exact failure that motivated the
feature: a hive fabricating a parallel stack (Vue/pytest in a Flutter app) to
make a fake test pass.
"""

from __future__ import annotations

from pathlib import Path

from gateway.crew_board.claude_runner import (
    _stack_hint,
    _UNSTUCK_PROMPT_TEMPLATE,
)


def test_stack_hint_detects_flutter(tmp_path: Path) -> None:
    (tmp_path / "pubspec.yaml").write_text("name: x\n", encoding="utf-8")
    hint = _stack_hint(str(tmp_path))
    assert "Flutter" in hint


def test_stack_hint_detects_python(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert "Python" in _stack_hint(str(tmp_path))


def test_stack_hint_unknown_when_no_markers(tmp_path: Path) -> None:
    assert "unknown" in _stack_hint(str(tmp_path)).lower()


def test_unstuck_prompt_forbids_fabricating_a_stack() -> None:
    # The guardrail must explicitly tell Claude not to fake files / a parallel
    # stack just to pass a test — the T-0351 failure mode.
    body = _UNSTUCK_PROMPT_TEMPLATE.lower()
    assert "do not fabricate" in body
    assert "parallel stack" in body
    # And it must require diagnosing before acting.
    assert "diagnose" in body


def test_unstuck_prompt_formats_with_task_fields() -> None:
    out = _UNSTUCK_PROMPT_TEMPLATE.format(
        project_name="demo", project_path="C:/x", stack_hint="Flutter/Dart",
        slug="T-9999", title="Do a thing", body="desc",
        criteria="  - one", last_action="turn 3 run_cmd pytest",
    )
    assert "T-9999" in out and "Flutter/Dart" in out and "turn 3" in out


# --- greenfield stack detector (T-0360 root cause: pytest hallucinated on Android) ---

def test_greenfield_android_is_not_pytest():
    from gateway.routes.board import _greenfield_stack
    d = _greenfield_stack("Build a Tetris game for Android")
    assert d["test_cmd"] == "flutter test"
    assert "pytest" not in d["test_cmd"]
    assert "Flutter" in d["directive"]


def test_greenfield_stack_matrix():
    from gateway.routes.board import _greenfield_stack
    assert _greenfield_stack("A React dashboard website")["test_cmd"] == "npm test"
    assert _greenfield_stack("A Rust CLI tool")["test_cmd"] == "cargo test"
    assert _greenfield_stack("A Python FastAPI service")["test_cmd"] == "python -m pytest -q"
    assert _greenfield_stack("Android native game in Kotlin with Gradle")["test_cmd"] == "gradlew.bat test"


def test_unstuck_uses_valid_assignee():
    # "claude" is NOT a valid ASSIGNEE (it's "claude-code") — guard the 500 regression.
    from gateway.crew_board import schema
    assert "claude-code" in schema.ASSIGNEES
    assert "claude" not in schema.ASSIGNEES


# --- fail-fast mixed-stack guard + scaffold (durable: stop the wedge at decompose) ---

def test_plan_guard_catches_wrong_stack():
    from gateway.routes.board import _greenfield_stack, _plan_stack_violations
    k = _greenfield_stack("Build a Tetris game for Android")  # Flutter
    bad = {"tickets": [{"title": "Scaffold",
                        "files": ["src/main/java/com/tetris/Grid.java", "tests/test_x.py"],
                        "criteria": ["pytest passes"]}]}
    assert _plan_stack_violations(bad, k["bad_globs"]), "guard missed java+pytest"


def test_plan_guard_passes_clean_dart():
    from gateway.routes.board import _greenfield_stack, _plan_stack_violations
    k = _greenfield_stack("Build a Tetris game for Android")
    ok = {"tickets": [{"title": "Models",
                       "files": ["lib/models/board.dart", "test/board_test.dart"],
                       "criteria": ["flutter test passes"]}]}
    assert not _plan_stack_violations(ok, k["bad_globs"]), "false positive on clean dart"


def test_greenfield_has_scaffold_kind():
    from gateway.routes.board import _greenfield_stack as g
    assert g("flutter app")["scaffold_kind"] == "flutter"
    assert g("a rust cli")["scaffold_kind"] == "rust"
    assert g("a python service")["scaffold_kind"] == "python"
