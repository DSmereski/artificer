"""Git activity route.

GET /v1/git/activity — recent commits across the enabled crew-board projects,
so the dashboard can show what the Hive has been shipping. Loopback-exempt like
the other dashboard reads. Best-effort + hard-timeouts; a non-repo or missing
path is simply skipped.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from gateway.deps import require_device_or_loopback

log = logging.getLogger("gateway.gitactivity")

router = APIRouter(prefix="/v1/git", tags=["git"])

_GIT_TIMEOUT_S = 3.0
_PER_REPO = 4      # commits per project
_MAX_TOTAL = 24


class Commit(BaseModel):
    project: str
    hash: str
    subject: str
    author: str
    ts: int        # author unix time


class GitActivity(BaseModel):
    commits: list[Commit] = []


def _repo_commits(project: str, path: str) -> list[Commit]:
    p = Path(path)
    if not (p / ".git").exists():
        return []
    try:
        proc = subprocess.run(
            ["git", "-C", str(p), "log", f"-{_PER_REPO}",
             "--format=%h\x1f%s\x1f%an\x1f%at"],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    out: list[Commit] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        h, subj, author, at = parts
        try:
            ts = int(at)
        except ValueError:
            ts = 0
        out.append(Commit(project=project, hash=h, subject=subj[:120],
                           author=author, ts=ts))
    return out


@router.get("/activity", response_model=GitActivity)
def git_activity(
    request: Request,
    device=Depends(require_device_or_loopback),
) -> GitActivity:
    """Recent commits across enabled crew projects, newest first."""
    store = getattr(request.app.state, "crew_store", None)
    if store is None:
        return GitActivity()
    commits: list[Commit] = []
    try:
        projects = store.list_projects()
    except Exception:  # noqa: BLE001
        projects = []
    for proj in projects:
        if not getattr(proj, "enabled", True):
            continue
        path = getattr(proj, "path", None)
        if not path:
            continue
        commits.extend(_repo_commits(proj.slug, path))
    commits.sort(key=lambda c: c.ts, reverse=True)
    return GitActivity(commits=commits[:_MAX_TOTAL])
