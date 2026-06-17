"""Filesystem -> note parsing helpers + watchdog handler.

Uses the shared utilities in ``vault_writer.util`` so frontmatter parsing,
audience coercion, and size limits stay consistent across the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler

from vault_writer.util import (
    MAX_NOTE_FILE_BYTES,
    coerce_audience,
    parse_frontmatter,
)


@dataclass(frozen=True, slots=True)
class NoteContent:
    rel_path: str
    note_type: str
    author: str
    audience: tuple[str, ...]
    frontmatter: dict
    body: str


_TYPE_FROM_FOLDER = {
    "canon": "canon",
    "people": "person",
    "sessions": "session",
    "ops": "ops",
    "journals": "journal",
    "knowledge": "knowledge",
    "system": "system",
    "projects": "project",
    "tools": "tool",
}


class NoteTooLarge(ValueError):
    """Raised when a markdown file exceeds MAX_NOTE_FILE_BYTES."""


def parse_note(path: Path, vault_root: Path) -> NoteContent:
    """Read a markdown file and return a NoteContent.

    Raises ValueError if path is outside the vault or NoteTooLarge
    when the file exceeds the configured size cap.
    """
    try:
        rel = path.resolve().relative_to(vault_root.resolve())
    except ValueError as e:
        raise ValueError(f"path is outside vault: {path}") from e

    try:
        size = path.stat().st_size
    except OSError as e:
        raise ValueError(f"cannot stat {path}: {e}") from e
    if size > MAX_NOTE_FILE_BYTES:
        raise NoteTooLarge(f"{path} is {size} bytes (>{MAX_NOTE_FILE_BYTES})")

    raw = path.read_text(encoding="utf-8", errors="replace")
    frontmatter, body = parse_frontmatter(raw)

    inferred_type = (
        _TYPE_FROM_FOLDER.get(rel.parts[0], "unknown") if rel.parts else "unknown"
    )
    note_type = str(frontmatter.get("type", inferred_type))
    author = str(frontmatter.get("author", "unknown"))
    audience = tuple(coerce_audience(frontmatter.get("audience")))

    return NoteContent(
        rel_path=rel.as_posix(),
        note_type=note_type,
        author=author,
        audience=audience,
        frontmatter=frontmatter,
        body=body,
    )


class VaultEventHandler(FileSystemEventHandler):
    """Dispatch markdown file events to a callback. Ignores non-.md events."""

    def __init__(
        self,
        vault_root: Path,
        on_change: Callable[[Path], None],
        on_delete: Callable[[Path], None],
    ) -> None:
        self._root = vault_root
        self._on_change = on_change
        self._on_delete = on_delete

    @staticmethod
    def _is_markdown(path_str: str) -> bool:
        return path_str.endswith(".md")

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory or not self._is_markdown(event.src_path):
            return
        self._on_change(Path(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory or not self._is_markdown(event.src_path):
            return
        self._on_change(Path(event.src_path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory or not self._is_markdown(event.src_path):
            return
        self._on_delete(Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src = getattr(event, "src_path", "")
        dst = getattr(event, "dest_path", "")
        if self._is_markdown(src):
            self._on_delete(Path(src))
        if self._is_markdown(dst):
            self._on_change(Path(dst))
