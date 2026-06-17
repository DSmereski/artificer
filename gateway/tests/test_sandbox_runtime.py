"""Tests for gateway.sandbox.python_runtime."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gateway.sandbox.python_runtime import run_python


@pytest.mark.asyncio
async def test_simple_expression_returns_repr():
    r = await run_python("1+2")
    assert r.ok, r
    assert r.return_value == "3"
    assert r.stdout == ""


@pytest.mark.asyncio
async def test_print_captured_in_stdout():
    r = await run_python("print('hi')")
    assert r.ok, r
    assert "hi" in r.stdout
    assert r.return_value == ""


@pytest.mark.asyncio
async def test_exception_marks_failure():
    r = await run_python("1/0")
    assert not r.ok
    assert r.error is not None and r.error.startswith("exit_")
    assert "ZeroDivisionError" in r.stderr


@pytest.mark.asyncio
async def test_timeout_kills_process():
    r = await run_python("import time; time.sleep(5)", timeout_s=0.5)
    assert not r.ok
    assert r.timed_out is True
    assert r.error == "timeout"
    assert r.duration_ms < 3000


@pytest.mark.asyncio
async def test_workdir_is_isolated():
    r1 = await run_python("import os; print(os.getcwd())")
    r2 = await run_python("import os; print(os.getcwd())")
    p1 = r1.stdout.strip().splitlines()[-1]
    p2 = r2.stdout.strip().splitlines()[-1]
    assert p1 != p2
    assert not Path(p1).exists()
    assert not Path(p2).exists()


@pytest.mark.asyncio
async def test_no_external_imports_required():
    src = Path(__file__).parent.parent / "sandbox" / "python_runtime.py"
    text = src.read_text(encoding="utf-8")
    for forbidden in ("import composio", "import httpx", "from composio", "from httpx"):
        assert forbidden not in text


@pytest.mark.asyncio
async def test_secrets_scrubbed_from_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_API_KEY", "sk-secret-must-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_must_not_leak")
    monkeypatch.setenv("COMPOSIO_API_KEY", "comp_must_not_leak")
    r = await run_python("import os; print(repr(sorted(os.environ.keys())))")
    assert r.ok, r
    out = r.stdout
    assert "CLAUDE_API_KEY" not in out
    assert "GITHUB_TOKEN" not in out
    assert "COMPOSIO_API_KEY" not in out
    # And the values themselves never appear (in case the test ever
    # printed values instead of keys).
    assert "sk-secret-must-not-leak" not in out
    assert "ghp_must_not_leak" not in out


@pytest.mark.asyncio
async def test_explicit_workdir_used():
    r = await run_python("import os; print(os.getcwd())", workdir=os.getcwd())
    assert r.ok, r
    assert r.stdout.strip().splitlines()[-1] == os.getcwd()
