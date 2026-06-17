"""Verifier — checks an in-progress task against its acceptance criteria.

Three signals:
  1. project test cmd ran cleanly
  2. files-of-interest globs match at least one path each
  3. acceptance criteria texts are either explicitly checked OR appear
     in the diff body (heuristic)

Records each as a structured `verify_results` dict so the UI can show
owner-friendly red/green next to each acceptance line.

Never raises. Failed verifications return ok=False with a reason.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from gateway.crew_board.store import CrewBoardStore, Task, Project

log = logging.getLogger("gateway.crew_board.verifier")


@dataclass
class VerifyResult:
    ok: bool
    tests: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)
    criteria: dict = field(default_factory=dict)
    reason: str = ""


def _augmented_env() -> dict:
    """os.environ with common dev-SDK bin dirs prepended to PATH.

    The gateway runs with a minimal PATH (often launched from a service /
    hidden window), so tools like `flutter`, `dart`, and `npm` may not be
    resolvable even though they are installed — which made `flutter test`
    fail to spawn and (before the gate fix) silently auto-pass. We prepend
    known SDK bins so configured test_cmds actually run. Honors FLUTTER_ROOT
    if set; otherwise probes a small set of standard install locations.
    """
    import os

    env = dict(os.environ)
    candidates: list[Path] = []
    flutter_root = env.get("FLUTTER_ROOT")
    if flutter_root:
        candidates.append(Path(flutter_root) / "bin")
    candidates += [
        Path("C:/src/flutter/bin"),
        Path("C:/flutter/bin"),
        Path.home() / "flutter" / "bin",
    ]
    extra = [str(p) for p in candidates if p.is_dir()]
    if extra:
        env["PATH"] = os.pathsep.join(extra) + os.pathsep + env.get("PATH", "")
    return env


def _run_tests(project: Project, *, timeout: float = 180.0) -> dict:
    """Run the project's test command. Returns a dict capturing
    stdout/stderr tails + return code."""
    if not project.test_cmd:
        return {
            "ran": False,
            "reason": "no test_cmd configured",
            "exit_code": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    project_dir = Path(project.path)
    if not project_dir.is_dir():
        return {
            "ran": False,
            "reason": f"project path missing: {project_dir}",
            "exit_code": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    env = _augmented_env()
    argv = shlex.split(project.test_cmd)
    # Windows: `flutter`/`npm` are .bat/.cmd shims. subprocess.run without a
    # shell looks for the bare name and ignores PATHEXT, so `["flutter", ...]`
    # raises WinError 2 even though flutter.bat is on PATH. Resolve argv[0] to
    # its real executable (shutil.which respects PATHEXT) against the augmented
    # PATH before spawning.
    if argv:
        import shutil
        resolved = shutil.which(argv[0], path=env.get("PATH"))
        if resolved:
            argv[0] = resolved
    try:
        proc = subprocess.run(
            argv,
            cwd=project_dir,
            capture_output=True, text=True,
            timeout=timeout, check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "ran": True,
            "exit_code": -1,
            "reason": f"timeout after {timeout}s",
            "stdout_tail": "",
            "stderr_tail": "",
        }
    except (OSError, ValueError) as e:
        return {
            "ran": False,
            "reason": f"could not spawn: {e}",
            "exit_code": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    return {
        "ran": True,
        "exit_code": proc.returncode,
        "reason": "",
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
    }


def _check_files(task: Task, project: Project) -> dict:
    """For each `files_of_interest` glob, count matching paths under
    the project dir. Glob is interpreted relative to the project."""
    project_dir = Path(project.path)
    out: dict = {"globs": [], "all_present": True}
    if not task.files_of_interest:
        out["all_present"] = True  # nothing required
        return out
    for glob in task.files_of_interest:
        try:
            matches = list(project_dir.glob(glob))
        except (OSError, ValueError):
            matches = []
        out["globs"].append({
            "glob": glob,
            "matches": [str(m.relative_to(project_dir)).replace("\\", "/")
                        for m in matches[:20]],
            "count": len(matches),
        })
        if not matches:
            out["all_present"] = False
    return out


def _check_criteria(task: Task) -> dict:
    """Owner manually ticks each acceptance criterion in the UI; this
    just reports the current state. We don't auto-infer from diff
    content — too noisy. The UI shows the unchecked ones."""
    items = task.acceptance_criteria or []
    checked = sum(1 for c in items if c.get("checked"))
    return {
        "total": len(items),
        "checked": checked,
        "all_checked": (checked == len(items)) if items else True,
        "unchecked": [
            c.get("text", "") for c in items if not c.get("checked")
        ],
    }


def verify(
    store: CrewBoardStore,
    task: Task,
    *,
    project: Project | None = None,
    run_tests: bool = True,
) -> VerifyResult:
    """Run all three signals; persist results into task.verify_results.
    `project` overrides the lookup so a parallel task can be verified
    against its own worktree checkout."""
    if project is None:
        project = store.get_project(task.project_slug)
    if project is None:
        return VerifyResult(
            ok=False, reason=f"unknown project {task.project_slug!r}",
        )
    tests = _run_tests(project) if run_tests else {"ran": False, "reason": "skipped"}
    files = _check_files(task, project)
    criteria = _check_criteria(task)
    # Smoke gate (A1+A7): a task may carry a `smoke_cmd` — a shell
    # command run AFTER pytest in the project dir. Non-zero exit fails
    # the tier. This catches "tests pass but the live binary is broken"
    # bugs like the all-black-fog ***REMOVED*** screen, where every
    # unit test mocked the state and the cross-module integration was
    # never exercised.
    smoke = _run_smoke(task, project) if run_tests else {"ran": False}
    # Auto-Ok rules (gates the move-to-review):
    #   - tests didn't fail (exit code 0 OR not configured)
    #   - all files-of-interest globs matched
    #   - smoke command (if any) exited 0
    # Note: acceptance criteria are checked by the owner during human
    # review, NOT by the verifier. Otherwise a fully-working agent run
    # would stall forever because nobody had ticked the boxes.
    # A test_cmd that is CONFIGURED but couldn't run (spawn failure or a
    # missing project path) must FAIL the gate — it is a broken environment,
    # not "no tests". Only a genuinely-absent test_cmd ("no test_cmd
    # configured") or an explicitly skipped run is permissive. This closes a
    # false-positive where e.g. `flutter test` failing to spawn auto-passed.
    _tests_reason = tests.get("reason", "") or ""
    _spawn_failed = (not tests.get("ran")) and _tests_reason.startswith(
        ("could not spawn", "project path missing")
    )
    tests_ok = (
        not _spawn_failed
        and (
            not tests.get("ran")  # skipped / no test_cmd is permissive
            or tests.get("exit_code") == 0
        )
    )
    smoke_ok = (
        not smoke.get("ran")  # no smoke_cmd configured is permissive
        or smoke.get("exit_code") == 0
    )
    ok = tests_ok and files.get("all_present", True) and smoke_ok
    reasons: list[str] = []
    if not tests_ok:
        if _spawn_failed:
            reasons.append(f"tests could not run: {_tests_reason}")
        else:
            reasons.append(
                f"tests failed (exit={tests.get('exit_code')})"
            )
    if not files.get("all_present", True):
        missing = [g["glob"] for g in files.get("globs", []) if g["count"] == 0]
        reasons.append(f"files missing: {missing}")
    if not smoke_ok:
        reasons.append(
            f"smoke failed (exit={smoke.get('exit_code')}): "
            f"{smoke.get('stderr_tail', '')[:200]}"
        )
    # criteria_unchecked is informational only — it does NOT make ok=False.
    if not criteria.get("all_checked", False):
        reasons.append(
            f"(owner-review) {criteria.get('total', 0) - criteria.get('checked', 0)} "
            f"criteria unchecked"
        )
    result = VerifyResult(
        ok=ok,
        tests=tests,
        files=files,
        criteria=criteria,
        reason="; ".join(reasons),
    )
    store.update_verify_results(task.slug, {
        "ok": result.ok,
        "tests": result.tests,
        "files": result.files,
        "criteria": result.criteria,
        "smoke": smoke,
        "reason": result.reason,
    })
    return result


def _run_smoke(task: Task, project: Project, *, timeout: float = 120.0) -> dict:
    """Run the task's `smoke_cmd` (if any) in the project dir. Non-zero
    exit means the integration is broken even though unit tests pass."""
    cmd = getattr(task, "smoke_cmd", None)
    if not cmd:
        return {"ran": False, "reason": "no smoke_cmd"}
    project_dir = Path(project.path)
    if not project_dir.is_dir():
        return {"ran": False, "reason": f"project path missing: {project_dir}"}
    try:
        proc = subprocess.run(
            shlex.split(cmd, posix=False),
            cwd=project_dir, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ran": True, "exit_code": -1,
            "reason": f"smoke timeout after {timeout}s",
            "stdout_tail": "", "stderr_tail": "",
        }
    except (OSError, ValueError) as e:
        return {
            "ran": True, "exit_code": -2,
            "reason": f"could not spawn smoke: {e}",
            "stdout_tail": "", "stderr_tail": "",
        }
    return {
        "ran": True,
        "exit_code": proc.returncode,
        "reason": "",
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
    }
