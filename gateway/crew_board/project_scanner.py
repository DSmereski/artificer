"""Auto-detect git repos under C:/Projects/ and upsert into the
crew_projects table. Owner toggles `enabled` to allow agents to
work the project.

Run once on gateway startup and every N minutes via the lifespan
background loop. Idempotent — re-detecting a known project just
refreshes path / name / test_cmd.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from gateway.crew_board.store import CrewBoardStore, Project

log = logging.getLogger("gateway.crew_board.scanner")

# Stem -> test command heuristic. Order matters: first match wins.
_TEST_CMD_BY_FILE = (
    # Use `python -m pytest` (not bare `pytest`) so we don't depend on
    # a `pytest.exe` shim being on PATH in the gateway process — esp.
    # on Windows where user-installed pytest often is not in PATH.
    ("pyproject.toml", "python -m pytest -q"),
    ("setup.py", "python -m pytest -q"),
    ("package.json", "npm test"),
    ("Cargo.toml", "cargo test"),
    ("go.mod", "go test ./..."),
    ("pubspec.yaml", "flutter test"),
)


@dataclass
class ScanResult:
    added: int
    updated: int
    seen: list[str]


def _slugify(name: str) -> str:
    """Lower-case kebab. Skip non-alphanumeric chars."""
    out: list[str] = []
    prev_sep = False
    for c in name:
        if c.isalnum():
            out.append(c.lower())
            prev_sep = False
        elif not prev_sep:
            out.append("-")
            prev_sep = True
    return "".join(out).strip("-")


def _detect_test_cmd(repo: Path) -> str | None:
    for fname, cmd in _TEST_CMD_BY_FILE:
        if (repo / fname).is_file():
            return cmd
    return None


def _norm_path(p: str) -> str:
    """Normalise a path for identity comparison: forward slashes, lower-case,
    no trailing slash. So 'C:\\Projects\\Foo' and 'c:/projects/foo/' match."""
    return p.replace("\\", "/").rstrip("/").lower()


def scan(
    store: CrewBoardStore,
    root: Path = Path(r"C:/Projects"),
) -> ScanResult:
    """Walk `root`, register every directory that is a git repo."""
    added = 0
    updated = 0
    seen: list[str] = []
    if not root.is_dir():
        log.info("project scanner: root %s does not exist", root)
        return ScanResult(0, 0, [])
    # Map every already-registered path → its slug so we never mint a SECOND
    # project for a directory that is already owned under a different slug. This
    # is what produced the duplicate twins (decompose registers a kebab slug for
    # a squashed dir name; the scanner then re-derived a squashed slug from the
    # same dir → a disabled 0-task duplicate). Path identity is the truth.
    path_owner = {_norm_path(p.path): p.slug for p in store.list_projects()}
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        if not (entry / ".git").exists():
            continue
        slug = _slugify(entry.name)
        if not slug:
            continue
        owner = path_owner.get(_norm_path(str(entry)))
        if owner is not None and owner != slug:
            # This directory already belongs to another slug — refresh that
            # canonical project's derived fields instead of duplicating it.
            canon = store.get_project(owner)
            if canon is not None:
                canon.path = str(entry).replace("\\", "/")
                canon.test_cmd = _detect_test_cmd(entry)
                store.upsert_project(canon)
                updated += 1
            seen.append(owner)
            continue
        seen.append(slug)
        existing = store.get_project(slug)
        p = Project(
            slug=slug,
            path=str(entry).replace("\\", "/"),
            name=entry.name,
            test_cmd=_detect_test_cmd(entry),
        )
        if existing is None:
            store.upsert_project(p)
            added += 1
        else:
            # Preserve owner-toggled fields; only refresh derived ones.
            p.enabled = existing.enabled
            p.push_allowed = existing.push_allowed
            store.upsert_project(p)
            updated += 1
    log.info(
        "project scanner: %d new, %d refreshed, %d total under %s",
        added, updated, len(seen), root,
    )
    return ScanResult(added=added, updated=updated, seen=seen)


def ensure_project_for_path(
    store: CrewBoardStore, path: Path,
    *, enabled: bool = True,
) -> Project:
    """Used when the board itself wants to create a NEW project (e.g.
    owner asks 'make me a blackjack game' which requires a fresh repo).
    Creates the directory if missing, initialises git, registers it
    with enabled=True by default since the owner just spoke it into
    existence."""
    path = Path(str(path).replace("\\", "/"))
    path.mkdir(parents=True, exist_ok=True)
    git_dir = path / ".git"
    if not git_dir.exists():
        import subprocess
        try:
            subprocess.run(
                ["git", "init", "-q"],
                cwd=path, check=True, capture_output=True, timeout=20,
            )
        except (subprocess.SubprocessError, OSError) as e:
            log.warning("git init failed for %s: %s", path, e)
    slug = _slugify(path.name)
    existing = store.get_project(slug)
    p = Project(
        slug=slug,
        path=str(path).replace("\\", "/"),
        name=path.name,
        enabled=enabled if existing is None else existing.enabled,
        push_allowed=False if existing is None else existing.push_allowed,
        test_cmd=_detect_test_cmd(path),
    )
    store.upsert_project(p)
    if existing is None and enabled:
        store.set_project_enabled(slug, enabled=True)
    return store.get_project(slug)  # type: ignore[return-value]
