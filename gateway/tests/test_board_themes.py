"""Tests for multi-theme support on the Crew Board.

Verifies:
1. The board HTML contains all 8 theme blocks (html[data-theme="..."]).
2. Each theme block defines --bg with a distinct value (swimlane bg differs).
3. Each theme block defines --panel (swimlane container) — also distinct.
4. The no-flash theme-sync <script> (localStorage read + postMessage listener)
   is present in <head> before any other scripts.
5. The board :root still defines the default token set (nothing broken).
6. joker and nod --bg values are present and distinct from each other and all
   other themes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gateway.crew_board.store import CrewBoardStore, Project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

THEMES = ["holo", "terminal", "brutalist", "vector-tron", "glitch-mag", "hive-v2",
          "joker", "nod"]


def _install_crew_store(client: TestClient, tmp_path: Path) -> CrewBoardStore:
    store = CrewBoardStore(tmp_path / "theme_test.db")
    store.upsert_project(
        Project(
            slug="theme-proj",
            path=str(tmp_path / "theme-proj"),
            name="Theme Project",
            enabled=True,
            push_allowed=False,
            test_cmd=None,
        )
    )
    client.app.state.crew_store = store
    return store


def _board_html(client: TestClient, tmp_path: Path) -> str:
    _install_crew_store(client, tmp_path)
    r = client.get("/board")
    assert r.status_code == 200, r.text
    return r.text


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_board_has_all_eight_theme_blocks(client: TestClient, tmp_path: Path) -> None:
    """All 8 html[data-theme=...] blocks must be present in the board HTML."""
    html = _board_html(client, tmp_path)
    for theme in THEMES:
        selector = f'html[data-theme="{theme}"]'
        assert selector in html, (
            f"Missing theme block for '{theme}' — expected '{selector}' in board HTML"
        )


def test_board_theme_bg_values_are_distinct(client: TestClient, tmp_path: Path) -> None:
    """--bg values in each theme block must all differ (swimlane bg recolors).

    Extracts the first --bg: ... declaration inside each theme block and checks
    that no two themes share the same value.
    """
    html = _board_html(client, tmp_path)
    bg_values: dict[str, str] = {}
    for theme in THEMES:
        # Match: html[data-theme="<theme>"] { ... --bg: <value>; ... }
        # Use a non-greedy pattern to grab the block, then find --bg inside it.
        block_pat = re.compile(
            rf'html\[data-theme="{re.escape(theme)}"\]\s*\{{([^}}]+)\}}',
            re.DOTALL,
        )
        m = block_pat.search(html)
        assert m, f"Could not locate theme block for '{theme}'"
        block = m.group(1)
        bg_pat = re.compile(r'--bg\s*:\s*([^;]+);')
        bm = bg_pat.search(block)
        assert bm, f"Theme '{theme}' block has no --bg declaration"
        bg_values[theme] = bm.group(1).strip()

    # All --bg values must be distinct across the 6 themes.
    unique = set(bg_values.values())
    assert len(unique) == len(THEMES), (
        f"Expected {len(THEMES)} distinct --bg values, got {len(unique)}. "
        f"Values: {bg_values}"
    )


def test_board_theme_panel_values_are_distinct(client: TestClient, tmp_path: Path) -> None:
    """--panel (swimlane background) must differ across themes."""
    html = _board_html(client, tmp_path)
    panel_values: dict[str, str] = {}
    for theme in THEMES:
        block_pat = re.compile(
            rf'html\[data-theme="{re.escape(theme)}"\]\s*\{{([^}}]+)\}}',
            re.DOTALL,
        )
        m = block_pat.search(html)
        assert m, f"Could not locate theme block for '{theme}'"
        block = m.group(1)
        pp = re.compile(r'--panel\s*:\s*([^;]+);')
        pm = pp.search(block)
        assert pm, f"Theme '{theme}' block has no --panel declaration"
        panel_values[theme] = pm.group(1).strip()

    unique = set(panel_values.values())
    assert len(unique) == len(THEMES), (
        f"Expected {len(THEMES)} distinct --panel values, got {len(unique)}. "
        f"Values: {panel_values}"
    )


def test_board_has_no_flash_theme_sync_script(client: TestClient, tmp_path: Path) -> None:
    """The board <head> must contain the theme-sync script before other scripts.

    Checks: localStorage.getItem('hive.theme') is read, and a 'storage' event
    listener and 'message' event listener are wired for live recolor.
    """
    html = _board_html(client, tmp_path)
    # Grab the <head> section
    head_match = re.search(r'<head>(.*?)</head>', html, re.DOTALL | re.IGNORECASE)
    assert head_match, "No <head>...</head> block found"
    head = head_match.group(1)

    assert "localStorage.getItem('hive.theme')" in head, (
        "Theme-sync script must read localStorage 'hive.theme' in <head>"
    )
    assert "addEventListener('storage'" in head, (
        "Theme-sync script must add a 'storage' event listener for live recolor"
    )
    assert "addEventListener('message'" in head, (
        "Theme-sync script must add a 'message' event listener for postMessage recolor"
    )
    assert "dataset.theme" in head, (
        "Theme-sync script must set document.documentElement.dataset.theme"
    )


def test_board_root_still_has_default_tokens(client: TestClient, tmp_path: Path) -> None:
    """:root must still define the core design tokens (nothing regressed)."""
    html = _board_html(client, tmp_path)
    required = ["--bg", "--panel", "--card", "--line", "--txt", "--accent",
                "--green", "--cyan", "--red", "--copper", "--font-ui"]
    # Find the :root block
    root_pat = re.compile(r':root\s*\{([^}]+)\}', re.DOTALL)
    m = root_pat.search(html)
    assert m, ":root block not found in board HTML"
    root_block = m.group(1)
    for tok in required:
        assert tok in root_block, f"Token '{tok}' missing from :root block"


def test_board_hive_v2_is_default_in_sync_script(client: TestClient, tmp_path: Path) -> None:
    """The theme-sync script must default to 'hive-v2' when no theme is stored."""
    html = _board_html(client, tmp_path)
    head_match = re.search(r'<head>(.*?)</head>', html, re.DOTALL | re.IGNORECASE)
    assert head_match
    head = head_match.group(1)
    assert "hive-v2" in head, (
        "Theme-sync default must be 'hive-v2' when localStorage has no value"
    )


def test_board_joker_nod_bg_present_and_distinct(client: TestClient, tmp_path: Path) -> None:
    """joker and nod --bg must be present in the board HTML and distinct from
    every other theme's --bg (including each other)."""
    html = _board_html(client, tmp_path)
    bg_values: dict[str, str] = {}
    for theme in THEMES:
        block_pat = re.compile(
            rf'html\[data-theme="{re.escape(theme)}"\]\s*\{{([^}}]+)\}}',
            re.DOTALL,
        )
        m = block_pat.search(html)
        assert m, f"Could not locate theme block for '{theme}'"
        block = m.group(1)
        bg_pat = re.compile(r'--bg\s*:\s*([^;]+);')
        bm = bg_pat.search(block)
        assert bm, f"Theme '{theme}' block has no --bg declaration"
        bg_values[theme] = bm.group(1).strip()

    # joker and nod must each appear and be distinct from all others.
    assert "joker" in bg_values, "joker theme block missing --bg"
    assert "nod" in bg_values, "nod theme block missing --bg"
    assert bg_values["joker"] != bg_values["nod"], (
        "joker and nod --bg values must differ"
    )
    # Verify full set uniqueness (8 distinct values).
    unique = set(bg_values.values())
    assert len(unique) == len(THEMES), (
        f"Expected {len(THEMES)} distinct --bg values across all themes, "
        f"got {len(unique)}. Values: {bg_values}"
    )
