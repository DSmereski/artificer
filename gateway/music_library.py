"""Music library helper for local-folder audio scanning and sandboxing.

Scans a folder for audio files and returns track metadata dicts. Uses mutagen
for tags if available; falls back to filename heuristics if not installed.

Security: all path operations are sandboxed against an allow-listed set of
root directories. Any path that resolves outside those roots is rejected.
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("gateway.music_library")

# Audio extensions we scan for.
AUDIO_EXTENSIONS = frozenset({".mp3", ".flac", ".wav", ".m4a", ".ogg", ".opus"})

# --- mutagen: optional dependency -----------------------------------------
try:
    import mutagen  # noqa: F401
    from mutagen import File as _MutagenFile
    _MUTAGEN_AVAILABLE = True
except ImportError:
    _MutagenFile = None  # type: ignore[assignment,misc]
    _MUTAGEN_AVAILABLE = False

# Default Windows Music folder (falls back gracefully if it doesn't exist).
_DEFAULT_MUSIC_ROOT = Path(os.path.expandvars(r"%USERPROFILE%\Music"))


# ---------------------------------------------------------------------------
# Track ID helpers
# ---------------------------------------------------------------------------

def track_id_for_path(path: Path) -> str:
    """Return a stable, URL-safe hex ID derived from the absolute path."""
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Filename heuristics (fallback when mutagen is missing)
# ---------------------------------------------------------------------------

_TRACK_NO_RE = re.compile(r"^(\d+)[.\-\s]+")


def _title_from_filename(path: Path) -> str:
    stem = path.stem
    # Strip leading track number ("01 - Title" → "Title")
    stem = _TRACK_NO_RE.sub("", stem).strip()
    # Replace underscores/dots with spaces
    return re.sub(r"[_\.]+", " ", stem).strip() or path.stem


def _track_no_from_filename(path: Path) -> int | None:
    m = _TRACK_NO_RE.match(path.stem)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------

def _extract_tags_mutagen(path: Path) -> dict[str, Any]:
    """Extract tags via mutagen. Returns {} on any failure."""
    if not _MUTAGEN_AVAILABLE or _MutagenFile is None:
        return {}
    try:
        af = _MutagenFile(str(path), easy=True)
        if af is None:
            return {}
        info = getattr(af, "info", None)
        duration_s = getattr(info, "length", None)

        def _first(tag: str) -> str | None:
            val = af.get(tag)
            return str(val[0]).strip() if val else None

        def _int_first(tag: str) -> int | None:
            val = _first(tag)
            if val is None:
                return None
            try:
                return int(val.split("/")[0])
            except (ValueError, AttributeError):
                return None

        return {
            "title": _first("title"),
            "artist": _first("artist"),
            "album": _first("album"),
            "track_no": _int_first("tracknumber"),
            "duration_s": round(duration_s, 2) if duration_s else None,
        }
    except Exception as exc:  # noqa: BLE001
        log.debug("mutagen failed on %s: %s", path, exc)
        return {}


def _extract_tags_fallback(path: Path) -> dict[str, Any]:
    """Filename heuristics used when mutagen is absent or fails."""
    return {
        "title": _title_from_filename(path),
        "artist": None,
        "album": None,
        "track_no": _track_no_from_filename(path),
        "duration_s": None,
    }


def extract_metadata(path: Path) -> dict[str, Any]:
    """Return a metadata dict for an audio file, preferring mutagen tags."""
    tags = _extract_tags_mutagen(path) if _MUTAGEN_AVAILABLE else {}
    fallback = _extract_tags_fallback(path)

    return {
        "id": track_id_for_path(path),
        "path": str(path),
        "title": tags.get("title") or fallback["title"],
        "artist": tags.get("artist") or fallback["artist"],
        "album": tags.get("album") or fallback["album"],
        "duration_s": tags.get("duration_s") or fallback["duration_s"],
        "track_no": tags.get("track_no") or fallback["track_no"],
    }


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------

class PathSandbox:
    """Allow-listed root directories.

    All path operations are guarded: ``resolve_safe`` raises ``ValueError``
    for any path that resolves outside the union of allowed roots.
    """

    def __init__(self, roots: list[Path]) -> None:
        self._roots: list[Path] = [r.resolve() for r in roots]

    @property
    def roots(self) -> list[Path]:
        return list(self._roots)

    def add_root(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved not in self._roots:
            self._roots.append(resolved)

    def is_allowed(self, path: Path) -> bool:
        """Return True if the resolved path is inside any allowed root."""
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError):
            return False
        return any(
            self._is_child_of(resolved, root) for root in self._roots
        )

    def resolve_safe(self, path: Path | str) -> Path:
        """Resolve path and return it, raising ValueError if sandboxed out."""
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError) as exc:
            raise ValueError(f"invalid path: {path}") from exc
        if not any(self._is_child_of(resolved, root) for root in self._roots):
            raise ValueError(
                f"path escapes allowed roots: {resolved!r} not under any of "
                + ", ".join(str(r) for r in self._roots)
            )
        return resolved

    @staticmethod
    def _is_child_of(child: Path, parent: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False


# ---------------------------------------------------------------------------
# Library scanning
# ---------------------------------------------------------------------------

def scan_folder(folder: Path, sandbox: PathSandbox) -> list[dict[str, Any]]:
    """Scan *folder* for audio files and return a list of track dicts.

    The folder must be inside the sandbox; raises ValueError otherwise.
    Non-audio files and unreadable entries are silently skipped.
    """
    safe_folder = sandbox.resolve_safe(folder)
    if not safe_folder.is_dir():
        return []

    tracks: list[dict[str, Any]] = []
    try:
        entries = sorted(safe_folder.iterdir())
    except PermissionError as exc:
        log.warning("scan_folder: cannot list %s: %s", safe_folder, exc)
        return []

    for entry in entries:
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        try:
            meta = extract_metadata(entry)
            tracks.append(meta)
        except Exception as exc:  # noqa: BLE001
            log.debug("scan_folder: skipping %s: %s", entry, exc)

    return tracks


def list_subdirectories(folder: Path, sandbox: PathSandbox) -> list[dict[str, Any]]:
    """Return immediate subdirectories of *folder* that are inside the sandbox.

    Each entry is ``{"name": str, "path": str}``.
    """
    safe_folder = sandbox.resolve_safe(folder)
    if not safe_folder.is_dir():
        return []

    result: list[dict[str, Any]] = []
    try:
        entries = sorted(safe_folder.iterdir())
    except PermissionError:
        return []

    for entry in entries:
        if not entry.is_dir():
            continue
        if not sandbox.is_allowed(entry):
            continue
        result.append({"name": entry.name, "path": str(entry)})
    return result


# ---------------------------------------------------------------------------
# Track registry (path→id lookup, shared with the route)
# ---------------------------------------------------------------------------

class TrackRegistry:
    """In-process id→path map, rebuilt on demand from scan results."""

    def __init__(self) -> None:
        self._map: dict[str, str] = {}  # id → absolute path string

    def index(self, tracks: list[dict[str, Any]]) -> None:
        for t in tracks:
            self._map[t["id"]] = t["path"]

    def resolve(self, track_id: str) -> Path | None:
        path_str = self._map.get(track_id)
        return Path(path_str) if path_str else None


# ---------------------------------------------------------------------------
# Library folders persistence
# ---------------------------------------------------------------------------

class LibraryFolderStore:
    """Persists user-added library folder paths to a small JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._folders: list[str] = []
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._folders = [str(p) for p in data.get("folders", [])]
        except Exception as exc:  # noqa: BLE001
            log.warning("LibraryFolderStore: load failed: %s", exc)
            self._folders = []

    def _save(self) -> None:
        try:
            self._path.write_text(
                json.dumps({"folders": self._folders}, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("LibraryFolderStore: save failed: %s", exc)

    def all(self) -> list[str]:
        return list(self._folders)

    def add(self, path: str) -> None:
        if path not in self._folders:
            self._folders.append(path)
            self._save()

    def remove(self, path: str) -> None:
        if path in self._folders:
            self._folders.remove(path)
            self._save()


# ---------------------------------------------------------------------------
# Module-level singletons (shared by the route)
# ---------------------------------------------------------------------------

def _fixed_drive_roots() -> list[Path]:
    """Every fixed drive root on this machine (C:\\, D:\\, ...) so the user can
    browse to music ANYWHERE on their own box. This is a single-user, loopback
    wallpaper tool — the sandbox's job is to reject malformed/escaping paths, not
    to lock the owner out of their own files."""
    roots: list[Path] = []
    if os.name == "nt":
        import string
        for letter in string.ascii_uppercase:
            d = Path(f"{letter}:\\")
            try:
                if d.exists():
                    roots.append(d)
            except OSError:
                continue
    else:
        roots.append(Path("/"))
    return roots


def make_sandbox(extra_roots: list[Path] | None = None) -> PathSandbox:
    """Build a sandbox rooted at the machine's drives + the default music root."""
    roots: list[Path] = list(_fixed_drive_roots())
    if _DEFAULT_MUSIC_ROOT.exists() and _DEFAULT_MUSIC_ROOT not in roots:
        roots.append(_DEFAULT_MUSIC_ROOT)
    if extra_roots:
        roots.extend(extra_roots)
    if not roots:                       # fallback so there's always >=1 root
        roots.append(_DEFAULT_MUSIC_ROOT)
    return PathSandbox(roots)


def content_type_for(path: Path) -> str:
    ext = path.suffix.lower()
    _MAP = {
        ".mp3":  "audio/mpeg",
        ".flac": "audio/flac",
        ".wav":  "audio/wav",
        ".m4a":  "audio/mp4",
        ".ogg":  "audio/ogg",
        ".opus": "audio/ogg; codecs=opus",
    }
    return _MAP.get(ext) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
