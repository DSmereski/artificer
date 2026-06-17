"""Each default scanner module exposes `scan` as a callable with the
Scanner Protocol shape: a `kind` attribute matching its bucket in
`KINDS`. groom_run logs by `getattr(scanner, "kind", "?")` — without
this, every crash log is unidentifiable."""

from __future__ import annotations

import pytest

from vault_writer.groomer.scanners import default_scanners
from vault_writer.groomer.suggestion import KINDS


@pytest.mark.parametrize("scanner", default_scanners())
def test_default_scanner_has_kind_attr(scanner) -> None:
    kind = getattr(scanner, "kind", None)
    assert isinstance(kind, str) and kind, (
        f"scanner {scanner!r} missing string `kind` attribute"
    )
    assert kind in KINDS, f"scanner kind {kind!r} not in KINDS {KINDS!r}"


@pytest.mark.parametrize("scanner", default_scanners())
def test_default_scanner_has_name_attr(scanner) -> None:
    name = getattr(scanner, "name", None)
    assert isinstance(name, str) and name
