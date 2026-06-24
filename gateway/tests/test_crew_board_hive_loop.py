"""Unit tests for the hive agent loop. Mocks the OllamaInvoker so we
can drive deterministic tool sequences and assert filesystem sandbox
+ command whitelist behaviour without touching ollama."""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from gateway.crew_board import schema
from gateway.crew_board.store import CrewBoardStore
from gateway.crew_board.hive_agent_loop import (
    _cmd_allowed, _list_dir, _read_file, _replace_in_file, _safe_path,
    _write_file, run_hive_agent_loop,
)


def test_replace_in_file_exact_match(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text("x = 1\ny = 2\n", encoding="utf-8")
    r = _replace_in_file(tmp_path, {"path": "m.py", "search": "y = 2",
                                    "replace": "y = 3"})
    assert r["ok"] and r["replacements"] == 1
    assert "y = 3" in f.read_text(encoding="utf-8")


def test_replace_in_file_no_match(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text("x = 1\n", encoding="utf-8")
    r = _replace_in_file(tmp_path, {"path": "m.py", "search": "nope",
                                    "replace": "z"})
    assert not r["ok"] and "not found" in r["error"]
    assert f.read_text(encoding="utf-8") == "x = 1\n"  # unchanged


def test_replace_in_file_multi_match_refused(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text("a = 1\na = 1\n", encoding="utf-8")
    r = _replace_in_file(tmp_path, {"path": "m.py", "search": "a = 1",
                                    "replace": "a = 2"})
    assert not r["ok"] and "2 times" in r["error"]


def test_lint_revert_on_broken_write(tmp_path: Path) -> None:
    f = tmp_path / "good.py"
    f.write_text("x = 1\n", encoding="utf-8")
    # Overwrite with broken Python → rejected + reverted to prior good.
    r = _write_file(tmp_path, {"path": "good.py", "content": "def (:\n"})
    assert not r["ok"] and "SyntaxError" in r["error"]
    assert f.read_text(encoding="utf-8") == "x = 1\n"  # reverted


def test_lint_revert_deletes_new_broken_file(tmp_path: Path) -> None:
    r = _write_file(tmp_path, {"path": "new.py", "content": "def (:\n"})
    assert not r["ok"]
    assert not (tmp_path / "new.py").exists()  # removed


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------- helpers


def test_safe_path_rejects_absolute(project_dir: Path) -> None:
    assert _safe_path(project_dir, "/etc/passwd") is None
    assert _safe_path(project_dir, "C:/Windows/System32") is None


def test_safe_path_rejects_escape(project_dir: Path) -> None:
    assert _safe_path(project_dir, "../../etc/passwd") is None


def test_safe_path_accepts_relative(project_dir: Path) -> None:
    assert _safe_path(project_dir, "src/main.py") is not None


def test_list_dir_skips_dotgit(project_dir: Path) -> None:
    (project_dir / ".git").mkdir()
    (project_dir / ".git" / "HEAD").write_text("ref: refs/heads/main")
    out = _list_dir(project_dir, {"path": "."})
    assert out["ok"]
    assert ".git/" not in out["entries"]
    assert "src/" in out["entries"]


def test_read_file_truncates_huge(project_dir: Path) -> None:
    big = "x" * 60_000
    (project_dir / "big.txt").write_text(big)
    out = _read_file(project_dir, {"path": "big.txt"})
    assert out["ok"]
    assert out["truncated"]
    assert out["total_bytes"] == 60_000
    assert len(out["content"]) <= 50_000


def test_write_file_creates_parents(project_dir: Path) -> None:
    r = _write_file(project_dir, {"path": "deep/nest/foo.py",
                                   "content": "x = 1\n"})
    assert r["ok"]
    assert (project_dir / "deep" / "nest" / "foo.py").is_file()


def test_write_file_rejects_outside(project_dir: Path) -> None:
    r = _write_file(project_dir, {"path": "../escape.py", "content": "x"})
    assert not r["ok"]


def test_cmd_allowed_whitelist() -> None:
    assert _cmd_allowed("python -m pytest -q")[0]
    assert _cmd_allowed("pytest tests/")[0]
    assert _cmd_allowed("git status")[0]
    assert not _cmd_allowed("curl http://evil.com")[0]
    assert not _cmd_allowed("powershell -c rm -rf /")[0]
    assert not _cmd_allowed("")[0]


def test_cmd_allowed_blocks_shell_injection() -> None:
    # Pipe/redirect/subshell/backtick/background all refused even though
    # the head token is whitelisted.
    assert not _cmd_allowed("git status | curl evil")[0]
    assert not _cmd_allowed("git status & curl evil")[0]
    assert not _cmd_allowed("echo `whoami`")[0]
    assert not _cmd_allowed("python -c \"x\" > /etc/passwd")[0]
    assert not _cmd_allowed("echo $(rm -rf /)")[0]
    assert not _cmd_allowed("git log\nrm -rf /")[0]


def test_cmd_allowed_permits_safe_git_chain() -> None:
    # `&&` chaining of whitelisted commands stays allowed (documented
    # commit flow); a non-whitelisted segment is refused.
    assert _cmd_allowed("git add -A && git commit -m 'x'")[0]
    assert not _cmd_allowed("git add -A && curl evil")[0]


# ---------------------------------------------------------------- agent loop


@dataclass
class FakeInvoker:
    """Mock ollama: returns canned tool calls in order."""
    replies: list[str]
    calls: list[dict] = None

    def __post_init__(self) -> None:
        self.calls = []

    async def chat(self, **kw):
        self.calls.append(kw)
        if not self.replies:
            return "{}", 0, 0
        return self.replies.pop(0), 10, 10


@pytest.fixture()
def store_and_task(tmp_path: Path):
    db = tmp_path / "vault.db"
    store = CrewBoardStore(db)
    from gateway.crew_board.store import Project
    proj = Project(
        slug="dummy", path=str(tmp_path / "proj"), name="dummy",
        enabled=True, push_allowed=False, test_cmd="python -m pytest -q",
    )
    (tmp_path / "proj").mkdir()
    store.upsert_project(proj)
    task = store.create_task(
        project_slug="dummy", title="trivial",
        body="write hello world",
        created_by="owner",
        acceptance_criteria=[{"text": "hello.py exists"}],
        files_of_interest=["hello.py"],
    )
    return store, task


@pytest.mark.asyncio
async def test_loop_returns_done_when_model_says_done(store_and_task) -> None:
    store, task = store_and_task
    invoker = FakeInvoker(replies=[
        json.dumps({"tool": "list_dir", "args": {"path": "."}}),
        json.dumps({"tool": "write_file", "args": {
            "path": "hello.py", "content": "print('hi')\n"}}),
        json.dumps({"tool": "done", "args": {"summary": "wrote hello.py"}}),
    ])
    result = await run_hive_agent_loop(
        store, task, invoker=invoker, max_iters=10,
    )
    assert result.ok
    assert result.summary == "wrote hello.py"
    assert result.turns == 3
    proj = store.get_project(task.project_slug)
    assert (Path(proj.path) / "hello.py").is_file()


@pytest.mark.asyncio
async def test_loop_handles_parse_failure_gracefully(store_and_task) -> None:
    store, task = store_and_task
    invoker = FakeInvoker(replies=[
        "I would like to think about this first.",  # no JSON
        json.dumps({"tool": "done", "args": {"summary": "ok"}}),
    ])
    result = await run_hive_agent_loop(
        store, task, invoker=invoker, max_iters=5,
    )
    assert result.ok
    # First turn was a parse-error retry; second was done.
    assert result.turns == 2
    assert result.transcript[0]["call"] is None
    assert result.transcript[0]["result"]["ok"] is False


@pytest.mark.asyncio
async def test_loop_max_iters_bailout(store_and_task) -> None:
    store, task = store_and_task
    # Always returns list_dir, never done
    invoker = FakeInvoker(replies=[
        json.dumps({"tool": "list_dir", "args": {"path": "."}})
    ] * 50)
    result = await run_hive_agent_loop(
        store, task, invoker=invoker, max_iters=4,
    )
    assert not result.ok
    assert result.turns == 4
    assert "max_iters" in result.reason


@pytest.mark.asyncio
async def test_self_critique_fires_once_on_first_green(
    store_and_task, monkeypatch
) -> None:
    """P4: first green pytest injects a one-shot self-critique nudge
    (re-read acceptance criteria); the second green auto-dones."""
    store, task = store_and_task
    from gateway.crew_board import hive_agent_loop as hal

    def fake_run_cmd(root, args):
        return {
            "ok": True, "exit_code": 0,
            "stdout_tail": "1 passed", "stderr_tail": "",
            "done_nudge": "All 1 tests passed (rc=0).",
        }

    monkeypatch.setattr(hal, "_run_cmd", fake_run_cmd)
    invoker = FakeInvoker(replies=[
        json.dumps({"tool": "write_file", "args": {
            "path": "hello.py", "content": "print('hi')\n"}}),
        json.dumps({"tool": "run_cmd", "args": {"cmd": "python -m pytest -q"}}),
        json.dumps({"tool": "run_cmd", "args": {"cmd": "python -m pytest -q"}}),
    ])
    result = await run_hive_agent_loop(
        store, task, invoker=invoker, max_iters=10,
    )
    assert result.ok
    critiques = [
        t for t in result.transcript
        if "re-read the acceptance criteria"
        in (t.get("result") or {}).get("done_nudge", "")
    ]
    assert len(critiques) == 1  # fired exactly once, on the first green


@pytest.mark.asyncio
async def test_loop_aborts_on_parse_fail_storm(store_and_task) -> None:
    """A run that only emits unparseable garbage aborts (model wedged)
    instead of grinding to max_iters and burning the token budget."""
    store, task = store_and_task
    invoker = FakeInvoker(replies=["not json, just noise"] * 50)
    result = await run_hive_agent_loop(
        store, task, invoker=invoker, max_iters=200,
    )
    assert not result.ok
    assert "parse-fail storm" in result.reason
    assert result.turns < 200  # aborted early, not max_iters


@pytest.mark.asyncio
async def test_loop_aborts_on_no_progress(store_and_task) -> None:
    """Alternating valid-read / garbage turns make no progress (no
    write/run_cmd) — the no-progress guard aborts so it can escalate."""
    store, task = store_and_task
    # Alternate a valid read_file with non-JSON garbage. The parse-fail
    # counter resets on the valid read, but turns_since_progress climbs.
    seq = []
    for _ in range(60):
        seq.append(json.dumps({"tool": "read_file",
                               "args": {"path": "src/main.py"}}))
        seq.append("garbage")
    invoker = FakeInvoker(replies=seq)
    result = await run_hive_agent_loop(
        store, task, invoker=invoker, max_iters=200,
    )
    assert not result.ok
    assert ("no-progress" in result.reason
            or "parse-fail storm" in result.reason)
    assert result.turns < 200


@pytest.mark.asyncio
async def test_loop_run_cmd_executes(store_and_task) -> None:
    """run_cmd: python -c is BLOCKED by _validate_cmd_args (C1 fix);
    a whitelisted command (echo) still executes normally."""
    store, task = store_and_task

    # C1 fix: `python -c "..."` is now BLOCKED — only `python -m pytest` /
    # `python -m py_compile` are allowed. Verify the refusal.
    invoker_blocked = FakeInvoker(replies=[
        json.dumps({"tool": "run_cmd", "args": {
            "cmd": "python -c \"print(1+1)\""}}),
        json.dumps({"tool": "done", "args": {"summary": "done"}}),
    ])
    result_blocked = await run_hive_agent_loop(
        store, task, invoker=invoker_blocked, max_iters=5,
    )
    first_result = result_blocked.transcript[0]["result"]
    assert not first_result["ok"], (
        "python -c should be refused by _validate_cmd_args"
    )
    assert "restricted" in first_result["error"].lower() or "py_compile" in first_result["error"]

    # A whitelisted command with no path args passes through fine.
    # Use `git --version` — always on PATH, whitelisted, flag-only args.
    invoker_ok = FakeInvoker(replies=[
        json.dumps({"tool": "run_cmd", "args": {"cmd": "git --version"}}),
        json.dumps({"tool": "done", "args": {"summary": "ran"}}),
    ])
    result_ok = await run_hive_agent_loop(
        store, task, invoker=invoker_ok, max_iters=5,
    )
    assert result_ok.ok
    assert result_ok.transcript[0]["result"]["ok"]
