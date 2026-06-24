"""Tests for board mutation auth (X-Board-Token / Bearer).

Verifies the design agreed in the security audit:
  - GET /board/state (read) requires loopback or device Bearer (H2 fix).
  - POST /board/tasks (mutation) without any token → 403.
  - POST /board/tasks with correct X-Board-Token → 200/4xx (not 403).
  - POST /board/tasks with valid device Bearer → 200/4xx (not 403).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gateway.crew_board.store import CrewBoardStore, Project
from gateway.routes.board import _BOARD_TOKEN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _install_crew_store(client: TestClient, tmp_path: Path) -> CrewBoardStore:
    """Wire a real (in-memory-backed) CrewBoardStore onto the test app."""
    store = CrewBoardStore(tmp_path / "board_auth_test.db")
    store.upsert_project(
        Project(
            slug="test-proj",
            path=str(tmp_path / "test-proj"),
            name="Test Project",
            enabled=True,
            push_allowed=False,
            test_cmd=None,
        )
    )
    client.app.state.crew_store = store
    return store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_board_state_open_no_auth(
    client: TestClient, tmp_path: Path
) -> None:
    """GET /board/state requires loopback or device Bearer (H2 security fix).

    Non-loopback anonymous callers → 401; loopback callers → 200.
    This test was updated after the H2 audit fix gated the read routes.
    """
    _install_crew_store(client, tmp_path)
    # Non-loopback (default TestClient host 'testclient') → 401.
    r = client.get("/board/state")
    assert r.status_code == 401, r.text

    # Loopback → 200 (no token needed).
    loopback = TestClient(client.app, client=("127.0.0.1", 51001))
    loopback.app.state.crew_store = client.app.state.crew_store
    r2 = loopback.get("/board/state")
    assert r2.status_code == 200, r2.text


def test_mutation_without_token_returns_403(
    client: TestClient, tmp_path: Path
) -> None:
    """POST /board/tasks without X-Board-Token or Bearer → 403."""
    _install_crew_store(client, tmp_path)
    r = client.post(
        "/board/tasks",
        json={"title": "should fail", "project_slug": "test-proj"},
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 403, f"expected 403, got {r.status_code}: {r.text}"


def test_mutation_with_wrong_token_returns_403(
    client: TestClient, tmp_path: Path
) -> None:
    """POST /board/tasks with a wrong X-Board-Token → 403."""
    _install_crew_store(client, tmp_path)
    r = client.post(
        "/board/tasks",
        json={"title": "bad token", "project_slug": "test-proj"},
        headers={
            "content-type": "application/json",
            "x-board-token": "not-the-right-token",
        },
    )
    assert r.status_code == 403, f"expected 403, got {r.status_code}: {r.text}"


def test_mutation_with_correct_board_token_passes_auth(
    client: TestClient, tmp_path: Path
) -> None:
    """POST /board/tasks with correct X-Board-Token → not 403 (board auth passed)."""
    _install_crew_store(client, tmp_path)
    r = client.post(
        "/board/tasks",
        json={"title": "auth test task", "project_slug": "test-proj"},
        headers={
            "content-type": "application/json",
            "x-board-token": _BOARD_TOKEN,
        },
    )
    # Board auth passed — endpoint ran. Any non-403 response means auth OK.
    assert r.status_code != 403, (
        f"expected auth to pass but got 403: {r.text}"
    )


def test_mutation_with_valid_bearer_passes_auth(
    client: TestClient, tmp_path: Path, paired_token: tuple[str, str]
) -> None:
    """POST /board/tasks with a valid device Bearer token → not 403."""
    _install_crew_store(client, tmp_path)
    _, token = paired_token
    r = client.post(
        "/board/tasks",
        json={"title": "bearer auth task", "project_slug": "test-proj"},
        headers={
            "content-type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    assert r.status_code != 403, (
        f"expected Bearer auth to pass but got 403: {r.text}"
    )


def test_pause_without_token_nonloopback_returns_403(
    client: TestClient, tmp_path: Path
) -> None:
    """POST /board/pause from a non-loopback host with no token → 403.

    The default TestClient host is 'testclient' (not loopback), so the
    operational loopback exemption must NOT apply here.
    """
    _install_crew_store(client, tmp_path)
    r = client.post("/board/pause")
    assert r.status_code == 403, f"expected 403, got {r.status_code}: {r.text}"


def test_pause_from_loopback_passes_auth(
    client: TestClient, tmp_path: Path
) -> None:
    """POST /board/pause from a loopback client → not 403 (no token needed).

    Mirrors the local restart script: pause/resume are loopback-exempt so the
    drain-then-restart flow needs no on-disk secret.
    """
    _install_crew_store(client, tmp_path)
    loopback = TestClient(client.app, client=("127.0.0.1", 54321))
    r = loopback.post("/board/pause")
    assert r.status_code != 403, (
        f"loopback pause should bypass token auth but got 403: {r.text}"
    )


def test_task_mutation_from_loopback_still_requires_token(
    client: TestClient, tmp_path: Path
) -> None:
    """The loopback exemption is scoped to pause/resume ONLY.

    POST /board/tasks from loopback with no token must STILL be 403 — task
    mutations are never loopback-exempt.
    """
    _install_crew_store(client, tmp_path)
    loopback = TestClient(client.app, client=("127.0.0.1", 54321))
    r = loopback.post(
        "/board/tasks",
        json={"title": "loopback no token", "project_slug": "test-proj"},
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 403, (
        f"task mutation must stay token-gated even on loopback: {r.text}"
    )


def test_session_token_loopback_only(client: TestClient, tmp_path: Path) -> None:
    """GET /board/session-token returns the token to loopback, 403 otherwise."""
    from gateway.routes.board import _BOARD_TOKEN
    _install_crew_store(client, tmp_path)

    # Non-loopback (default TestClient host 'testclient') → 403.
    r = client.get("/board/session-token")
    assert r.status_code == 403, r.text

    # Loopback → 200 with the real board token.
    loopback = TestClient(client.app, client=("127.0.0.1", 51000))
    r2 = loopback.get("/board/session-token")
    assert r2.status_code == 200, r2.text
    assert r2.json()["token"] == _BOARD_TOKEN


def test_board_html_contains_token_meta(
    client: TestClient, tmp_path: Path
) -> None:
    """GET /board HTML embeds the board token ONLY for loopback callers (C2 fix).

    Non-loopback callers get content="" so they cannot perform mutations.
    Loopback callers get the real token embedded.
    """
    _install_crew_store(client, tmp_path)
    # Non-loopback → empty token (vuln closed: the real token is never leaked).
    r = client.get("/board")
    assert r.status_code == 200, r.text
    assert 'content=""' in r.text, "non-loopback should get empty board token"
    assert f'content="{_BOARD_TOKEN}"' not in r.text, (
        "real token must NOT appear in HTML served to non-loopback callers"
    )

    # Loopback → real token embedded.
    loopback = TestClient(client.app, client=("127.0.0.1", 51002))
    loopback.app.state.crew_store = client.app.state.crew_store
    r2 = loopback.get("/board")
    assert r2.status_code == 200, r2.text
    assert f'content="{_BOARD_TOKEN}"' in r2.text, (
        "loopback caller should get the real board token in meta[name=board-token]"
    )


def test_board_html_has_csp_header(
    client: TestClient, tmp_path: Path
) -> None:
    """GET /board must set a Content-Security-Policy header."""
    _install_crew_store(client, tmp_path)
    r = client.get("/board")
    assert r.status_code == 200, r.text
    csp = r.headers.get("content-security-policy", "")
    assert csp, "board page must set Content-Security-Policy header"
    assert "script-src" in csp
    assert "frame-ancestors" in csp


def test_standalone_board_forbids_framing(
    client: TestClient, tmp_path: Path
) -> None:
    """GET /board (no embed) keeps frame-ancestors 'none' + no embed body class."""
    _install_crew_store(client, tmp_path)
    r = client.get("/board")
    assert r.status_code == 200, r.text
    assert "frame-ancestors 'none'" in r.headers.get("content-security-policy", "")
    assert '<body class="">' in r.text or "<body>" in r.text
    assert '<body class="embed">' not in r.text


def test_embed_mode_relaxes_csp_and_sets_body_class(
    client: TestClient, tmp_path: Path
) -> None:
    """GET /board?embed=1 → frame-ancestors DROPPED + <body class="embed">.

    The embed CSP omits frame-ancestors entirely so a file:// (opaque-origin)
    wallpaper parent can frame it — `*` does not match a file:// ancestor. The
    standalone /board keeps 'none'. /board is loopback-only + token-gated.

    C2 fix: the real board token is ONLY embedded for loopback callers.
    """
    _install_crew_store(client, tmp_path)
    r = client.get("/board?embed=1")
    assert r.status_code == 200, r.text
    csp = r.headers.get("content-security-policy", "")
    assert "frame-ancestors" not in csp
    assert '<body class="embed">' in r.text
    # Non-loopback: real token must NOT appear (C2 fix).
    assert 'content=""' in r.text, "non-loopback embed must get empty board token"

    # Loopback embed: real token must be present so the iframe can mutate.
    loopback = TestClient(client.app, client=("127.0.0.1", 51003))
    loopback.app.state.crew_store = client.app.state.crew_store
    r2 = loopback.get("/board?embed=1")
    assert r2.status_code == 200, r2.text
    assert f'content="{_BOARD_TOKEN}"' in r2.text, (
        "loopback embed caller must receive real board token"
    )
