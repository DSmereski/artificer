# vault_writer/groomer/tests/test_groom_run.py
"""Tests for run_groom orchestrator."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from vault_writer.groomer.groom_run import run_groom
from vault_writer.groomer.scanners import ScanContext
from vault_writer.groomer.suggestion import Suggestion


def _stub_scanner(suggestions: list[Suggestion]):
    def _impl(ctx: ScanContext) -> list[Suggestion]:
        return list(suggestions)
    _impl.kind = suggestions[0].kind if suggestions else "dup_scanner"
    return _impl


def _crashing_scanner(ctx: ScanContext) -> list[Suggestion]:
    raise RuntimeError("boom")
_crashing_scanner.kind = "link_scanner"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_run_groom_aggregates_suggestions(tmp_path: Path) -> None:
    s1 = Suggestion(kind="dup_scanner", slug="x", confidence=0.94, title="t", body_md="b")
    s2 = Suggestion(kind="link_scanner", slug="y", confidence=0.95, title="t", body_md="b")
    counts = await run_groom(
        vault_path=tmp_path,
        scanners=[_stub_scanner([s1]), _stub_scanner([s2])],
    )
    assert counts["dup_scanner"] == 1
    assert counts["link_scanner"] == 1
    assert (tmp_path / "ops" / "groomer" / "dup_scanner" / "x.md").exists()
    assert (tmp_path / "ops" / "groomer" / "link_scanner" / "y.md").exists()


@pytest.mark.asyncio
async def test_run_groom_isolates_crashing_scanner(tmp_path: Path) -> None:
    s1 = Suggestion(kind="dup_scanner", slug="x", confidence=0.94, title="t", body_md="b")
    counts = await run_groom(
        vault_path=tmp_path,
        scanners=[_stub_scanner([s1]), _crashing_scanner],
    )
    # Crashing scanner is recorded with 0 count; healthy scanner still ran.
    assert counts["dup_scanner"] == 1
    assert counts["link_scanner"] == 0


@pytest.mark.asyncio
async def test_run_groom_auto_opens_embedder_when_none_passed(
    tmp_path: Path, monkeypatch,
) -> None:
    """run_groom should auto-open an Embedder via the standard helper
    when none is injected by the caller, so contradiction_scanner can
    fire under the production idle-loop wiring (which never passes one
    explicitly)."""
    from vault_writer.groomer import groom_run as groom_run_mod

    class _FakeEmbedder:
        async def embed(self, text: str) -> list[float]:
            return [1.0, 0.0, 0.0]

    class _FakeClient:
        def __init__(self) -> None:
            self.aclose_called = False

        async def aclose(self) -> None:
            self.aclose_called = True

    fake_emb = _FakeEmbedder()
    fake_client = _FakeClient()

    def fake_open(_vault_path: Path):
        return fake_emb, fake_client

    monkeypatch.setattr(groom_run_mod, "_open_embedder_or_none", fake_open)

    captured: list[Any] = []

    def _capture(ctx: ScanContext) -> list[Suggestion]:
        captured.append(ctx.embedder)
        return []
    _capture.kind = "dup_scanner"  # type: ignore[attr-defined]

    await run_groom(vault_path=tmp_path, scanners=[_capture])

    assert captured == [fake_emb]
    assert fake_client.aclose_called, "auto-opened httpx client must be closed"


@pytest.mark.asyncio
async def test_run_groom_does_not_auto_open_when_embedder_passed(
    tmp_path: Path, monkeypatch,
) -> None:
    """If the caller injects an embedder, run_groom must not call the
    auto-open helper at all (no surprise httpx connections in tests)."""
    from vault_writer.groomer import groom_run as groom_run_mod

    def _explode(_vault_path: Path):
        raise AssertionError("auto-open helper must not be called")

    monkeypatch.setattr(groom_run_mod, "_open_embedder_or_none", _explode)

    class _Sentinel: ...
    sentinel = _Sentinel()

    captured: list[Any] = []

    def _capture(ctx: ScanContext) -> list[Suggestion]:
        captured.append(ctx.embedder)
        return []
    _capture.kind = "dup_scanner"  # type: ignore[attr-defined]

    await run_groom(vault_path=tmp_path, scanners=[_capture], embedder=sentinel)
    assert captured == [sentinel]


@pytest.mark.asyncio
async def test_run_groom_global_cap_drops_low_confidence(
    tmp_path: Path, monkeypatch,
) -> None:
    """Five scanners each emitting 50 proposals = 250 total; the global
    cap must trim the lowest-confidence ones first so the user only sees
    the most actionable items."""
    from vault_writer.groomer import groom_run as groom_run_mod
    monkeypatch.setattr(groom_run_mod, "MAX_SUGGESTIONS_PER_RUN", 10)

    high = [
        Suggestion(kind="dup_scanner", slug=f"hi{i}", confidence=0.95,
                   title="t", body_md="b")
        for i in range(5)
    ]
    low = [
        Suggestion(kind="link_scanner", slug=f"lo{i}", confidence=0.20,
                   title="t", body_md="b")
        for i in range(50)
    ]
    counts = await run_groom(
        vault_path=tmp_path,
        scanners=[_stub_scanner(high), _stub_scanner(low)],
    )
    # All 5 high-confidence kept; remainder filled from low until cap.
    assert counts["dup_scanner"] == 5
    assert counts["link_scanner"] == 5
    # Total written must equal cap.
    written = sum(
        1 for _ in (tmp_path / "ops" / "groomer").rglob("*.md")
        if "_runs" not in _.parts
    )
    assert written == 10
