"""Sandboxed Python execution runtime.

Spawns a fresh `python -I` subprocess in an isolated tempdir with a
wall-clock timeout and best-effort memory cap. Captures stdout/stderr
and the repr of the final expression (if any).

POSIX uses `resource.setrlimit(RLIMIT_AS, ...)` for hard memory cap.
Windows has no equivalent — psutil best-effort if available, otherwise
the wall-clock timeout is the real safety net.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

_RV_SENTINEL = "__HIVE_RV__"

_WRAPPER = r"""
import ast, json, os, resource as _r, sys, traceback

def _set_mem(mb):
    try:
        soft, hard = _r.getrlimit(_r.RLIMIT_AS)
        _r.setrlimit(_r.RLIMIT_AS, (mb * 1024 * 1024, hard))
    except Exception:
        pass

_cfg = json.loads(os.environ["HIVE_SANDBOX_CFG"])
if hasattr(__import__("builtins"), "object") and sys.platform != "win32":
    _set_mem(_cfg["mem_mb"])
os.chdir(_cfg["workdir"])

_code = _cfg["code"]
try:
    _tree = ast.parse(_code, mode="exec")
except SyntaxError:
    traceback.print_exc()
    sys.exit(2)

_g = {"__name__": "__hive_sandbox__", "__builtins__": __builtins__}
try:
    if _tree.body and isinstance(_tree.body[-1], ast.Expr):
        _last = _tree.body.pop()
        if _tree.body:
            exec(compile(ast.Module(body=_tree.body, type_ignores=[]), "<sandbox>", "exec"), _g)
        _val = eval(compile(ast.Expression(body=_last.value), "<sandbox>", "eval"), _g)
        if _val is not None:
            sys.stdout.write("\n%s\t%r\n" % ("__HIVE_RV__", _val))
    else:
        exec(compile(_tree, "<sandbox>", "exec"), _g)
except SystemExit:
    raise
except BaseException:
    traceback.print_exc()
    sys.exit(1)
"""

# Windows: drop the POSIX-only `resource` import.
_WRAPPER_WIN = _WRAPPER.replace("import ast, json, os, resource as _r, sys, traceback", "import ast, json, os, sys, traceback")
_WRAPPER_WIN = _WRAPPER_WIN.replace("def _set_mem(mb):\n    try:\n        soft, hard = _r.getrlimit(_r.RLIMIT_AS)\n        _r.setrlimit(_r.RLIMIT_AS, (mb * 1024 * 1024, hard))\n    except Exception:\n        pass\n\n", "def _set_mem(mb):\n    pass\n\n")


@dataclass(frozen=True)
class SandboxResult:
    ok: bool
    stdout: str
    stderr: str
    return_value: str
    duration_ms: int
    timed_out: bool
    error: str | None
    workdir: str


async def run_python(
    code: str,
    *,
    timeout_s: float = 30.0,
    mem_limit_mb: int = 512,
    workdir: str | os.PathLike[str] | None = None,
) -> SandboxResult:
    started = time.monotonic()
    cleanup_dir: str | None = None
    if workdir is None:
        cleanup_dir = tempfile.mkdtemp(prefix="hive_sandbox_")
        wd = cleanup_dir
    else:
        wd = str(workdir)

    cfg = json.dumps({"code": code, "workdir": wd, "mem_mb": int(mem_limit_mb)})
    # Scrubbed env: only pass what python needs to start. Secrets like
    # CLAUDE_API_KEY, COMPOSIO_API_KEY, GITHUB_TOKEN, SUPABASE_*,
    # NTFY_TOKEN, AWS_*, etc. live in os.environ and would leak into
    # arbitrary code via os.environ.get(...). The sandbox is critic-
    # gated but defense-in-depth: don't hand the keys over.
    base_env = {}
    for k in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE",
              "LANG", "LC_ALL", "PYTHONPATH"):
        v = os.environ.get(k)
        if v is not None:
            base_env[k] = v
    env = {**base_env, "HIVE_SANDBOX_CFG": cfg, "PYTHONIOENCODING": "utf-8"}
    wrapper = _WRAPPER_WIN if sys.platform == "win32" else _WRAPPER

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", wrapper,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=wd,
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            duration = int((time.monotonic() - started) * 1000)
            return SandboxResult(
                ok=False, stdout="", stderr="",
                return_value="", duration_ms=duration,
                timed_out=True, error="timeout", workdir=wd,
            )

        stdout = (out_b or b"").decode("utf-8", "replace")
        stderr = (err_b or b"").decode("utf-8", "replace")
        rv = ""
        if _RV_SENTINEL in stdout:
            # On Windows the wrapper writes "\n__HIVE_RV__\t<repr>\n" but
            # the child's stdout pipe converts \n → \r\n, so search for
            # the sentinel without the leading newline and strip CRLF.
            idx = stdout.rfind(_RV_SENTINEL + "\t")
            head = stdout[:idx].rstrip("\r\n")
            tail = stdout[idx + len(_RV_SENTINEL) + 1:].rstrip("\r\n")
            stdout, rv = head, tail
        rc = proc.returncode or 0
        duration = int((time.monotonic() - started) * 1000)
        if rc == 0:
            return SandboxResult(
                ok=True, stdout=stdout, stderr=stderr,
                return_value=rv, duration_ms=duration,
                timed_out=False, error=None, workdir=wd,
            )
        tail = stderr.strip().splitlines()[-1] if stderr.strip() else ""
        return SandboxResult(
            ok=False, stdout=stdout, stderr=stderr,
            return_value=rv, duration_ms=duration,
            timed_out=False, error=f"exit_{rc}: {tail}"[:200],
            workdir=wd,
        )
    finally:
        if cleanup_dir is not None:
            _rmtree_safe(cleanup_dir)


def _rmtree_safe(path: str) -> None:
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


__all__ = ["SandboxResult", "run_python"]
