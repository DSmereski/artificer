"""Groomer scanner protocol + registry.

Each scanner is a callable that takes a `ScanContext` and returns
`list[Suggestion]`. Importing this module lazy-imports the five
default scanners and registers them in `default_scanners()`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol


@dataclass
class ScanContext:
    """Inputs every scanner can use. Optional fields are None when
    the scanner doesn't need them — keeps the protocol simple."""
    vault_path: Path
    now_ts: float
    # Optional dependencies — populated by groom_run when available.
    vault_index: Any = None        # vault_writer.index.VaultIndex
    embedder: Any = None           # vault_writer.embedder.Embedder
    # Internal: memoised vault walk so N scanners share ONE rglob
    # + frontmatter-parse pass.
    _notes_cache: list | None = field(default=None, repr=False)

    def notes(self) -> list:
        """Return all NoteRecords for this run. Walks the vault once,
        then returns the cached list for every subsequent caller."""
        if self._notes_cache is None:
            from vault_writer.groomer.inputs import iter_vault_notes
            self._notes_cache = list(iter_vault_notes(self.vault_path))
        return self._notes_cache


class Scanner(Protocol):
    name: str
    kind: str

    def __call__(self, ctx: ScanContext) -> list:  # list[Suggestion]
        ...


def default_scanners() -> list[Scanner]:
    """Returns the five default scanners in REGISTRY order. Lazy-
    imports the modules so circular imports stay easy to spot."""
    from vault_writer.groomer.scanners import (  # noqa: WPS433
        dup_scanner, link_scanner, format_scanner,
        contradiction_scanner, stale_scanner,
    )
    return [
        dup_scanner.scan,
        link_scanner.scan,
        format_scanner.scan,
        contradiction_scanner.scan,
        stale_scanner.scan,
    ]
