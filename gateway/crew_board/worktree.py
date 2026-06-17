"""Git worktree helpers for parallel task lanes.

Why worktrees: two tasks sharing one checkout race on the index and on
`git checkout`. Each parallel task instead gets its own isolated
worktree under `<repo>/.crew-worktrees/<slug>` on branch `crew/<slug>`,
so N tasks can build concurrently with zero collision. The git
rollback-on-fail in the dispatcher is already scoped per-tree.

Mirrors the side-project `devteam/gitops.py` template, adapted to the
crew board's slug/branch naming.

All functions are best-effort and raise `WorktreeError` on a hard git
failure so the caller can fall back to the shared checkout.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# Defense-in-depth: slugs are system-generated `T-NNNN` today, but the
# branch/worktree-dir builders embed the slug into a git ref and a path.
# Refuse anything that isn't the expected shape so a future code path
# that lets user input become a slug can't inject `..`, `/`, or refspecs.
_SLUG_RE = re.compile(r"^[a-z]+-\d{1,8}$")

# Worktrees live INSIDE the repo (not repo.parent) so a relative
# project path stays self-contained and the dir is easy to .gitignore.
_WORKTREE_DIR = ".crew-worktrees"
_BRANCH_PREFIX = "crew/"


class WorktreeError(RuntimeError):
    """A git worktree operation failed."""


def _git(cwd: Path, *args: str, timeout: float = 120.0) -> str:
    """Run a git command in `cwd`. Raise WorktreeError on non-zero exit."""
    try:
        r = subprocess.run(
            ["git", *args], cwd=str(cwd),
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise WorktreeError(f"git {' '.join(args)}: {e}") from e
    if r.returncode != 0:
        raise WorktreeError(
            f"git {' '.join(args)} failed: {(r.stderr or '').strip()}"
        )
    return r.stdout


def _validate_slug(slug: str) -> str:
    s = (slug or "").lower()
    if not _SLUG_RE.match(s):
        raise WorktreeError(f"refusing unsafe slug for worktree: {slug!r}")
    return s


def branch_name(slug: str) -> str:
    return f"{_BRANCH_PREFIX}{_validate_slug(slug)}"


def worktree_path(repo: Path, slug: str) -> Path:
    """Isolated worktree dir for a task slug, inside the repo."""
    return Path(repo) / _WORKTREE_DIR / _validate_slug(slug)


def _current_branch(repo: Path) -> str:
    """Best-effort current branch; falls back to HEAD."""
    try:
        out = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        return out or "HEAD"
    except WorktreeError:
        return "HEAD"


def ensure_worktree(
    repo: Path, slug: str, base: str | None = None,
) -> Path:
    """Create (or reuse) an isolated worktree for `slug` on branch
    `crew/<slug>`. `base` defaults to the repo's current branch.
    Returns the worktree path."""
    repo = Path(repo)
    wt = worktree_path(repo, slug)
    if (wt / ".git").exists():
        return wt  # reuse existing
    branch = branch_name(slug)
    base = base or _current_branch(repo)
    wt.parent.mkdir(parents=True, exist_ok=True)
    # Reuse the branch if it already exists, else create it off base.
    existing = _git(repo, "branch", "--list", branch).strip()
    if existing:
        _git(repo, "worktree", "add", str(wt), branch)
    else:
        _git(repo, "worktree", "add", "-b", branch, str(wt), base)
    return wt


def merge_into_base(repo: Path, slug: str, base: str | None = None) -> bool:
    """Merge the task's `crew/<slug>` branch back into `base` (the repo's
    current branch by default) so verified work actually LANDS — without
    this, a parallel task commits only to its own branch and the next
    task branches off the unchanged base, never seeing it. Returns True
    on a clean merge; on conflict it aborts the merge and returns False
    (branch + worktree are left for manual resolution). Best-effort."""
    repo = Path(repo)
    try:
        branch = branch_name(slug)
    except WorktreeError:
        return False
    base = base or _current_branch(repo)
    # Nothing to merge if the branch doesn't exist.
    if not _git(repo, "branch", "--list", branch).strip():
        return False
    try:
        _git(repo, "merge", "--no-ff", "--no-edit", branch)
        return True
    except WorktreeError:
        # Conflict or other failure — abort so the base checkout is left
        # clean, and surface False so the caller can flag it.
        try:
            _git(repo, "merge", "--abort")
        except WorktreeError:
            pass
        return False


def remove_worktree(repo: Path, slug: str) -> None:
    """Remove the worktree once a task is done. The branch is left in
    place for merge/inspection. Best-effort — never raises."""
    repo = Path(repo)
    try:
        wt = worktree_path(repo, slug)
    except WorktreeError:
        return
    if not wt.exists():
        return
    try:
        _git(repo, "worktree", "remove", "--force", str(wt))
    except WorktreeError:
        # Fall back to pruning a stale registration.
        try:
            _git(repo, "worktree", "prune")
        except WorktreeError:
            pass
