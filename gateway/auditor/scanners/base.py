# gateway/auditor/scanners/base.py
"""Scanner protocol shared by every scanner module.

A scanner takes the auditor's pre-loaded inputs and returns zero or
more Findings. Scanners are pure (no side effects) so they're trivial
to unit-test against synthetic turn dicts.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from gateway.auditor.findings import Finding


@runtime_checkable
class Scanner(Protocol):
    name: str  # short identifier used in audit reports + scanner toggles

    def scan(
        self,
        *,
        turns: list[dict],
        memories: list[dict],
    ) -> list[Finding]: ...
