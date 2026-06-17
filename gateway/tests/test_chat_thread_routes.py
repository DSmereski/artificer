"""HTTP route tests for thread rename / unarchive / pin endpoints.

Tests the five scenarios from the 2026-05-08 chat-thread-sheet spec:
  1. PATCH rename sets title and title_locked
  2. PATCH rename rejects empty/whitespace title
  3. PATCH rename rejects requests from a different owner
  4. POST unarchive clears archived_at
  5. POST pin toggles the pinned flag
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.config import (
    Config,
    NtfyConfig,
    PairingConfig,
    RateLimits,
    VaultWriterConfig,
)
from gateway.deps import AppState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_client(
    tmp_path: Path,
    *,
    vault_client: Any = None,
) -> tuple[TestClient, str]:
    """Build a minimal TestClient with vault_client injected.

    Returns (client, bearer_token).
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()

    cfg = Config(
        bind_host="127.0.0.1",
        bind_port=0,
        tailscale_bind=None,
        state_dir=state_dir,
        vault_path=vault,
        vault_writer=VaultWriterConfig(
            host="127.0.0.1",
            port=8765,
            token_path=tmp_path / "does-not-exist",
        ),
        history_roots={},
        models={},
        pairing=PairingConfig(code_ttl_seconds=60, code_length=8, token_bytes=16),
        ntfy=NtfyConfig(base_url="http://127.0.0.1:8080", enabled=False),
        rate_limits=RateLimits(writes_per_minute=60, images_per_hour=30),
    )

    app = create_app(cfg)
    prev = app.state.ai_team

    fake_adapter = MagicMock()
    fake_adapter.name = "terry"

    app.state.ai_team = AppState(
        config=cfg,
        devices=prev.devices,
        pairing=prev.pairing,
        adapters={"terry": fake_adapter},
        vault_client=vault_client,
    )

    client = TestClient(app)

    r = client.get("/v1/pair/new")
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    r = client.post("/v1/pair", json={
        "code": code, "name": "test-device", "platform": "test",
    })
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    return client, token


def _make_thread_meta(
    thread_id: str,
    *,
    bot: str = "terry",
    user_id: int,
    archived_at: str | None = None,
    pinned: int = 0,
    title_locked: int = 0,
) -> dict:
    return {
        "id": thread_id,
        "bot": bot,
        "user_id": user_id,
        "title": "Old Title",
        "title_locked": title_locked,
        "pinned": pinned,
        "created_at": "2026-01-01T00:00:00",
        "last_active_at": "2026-01-01T00:00:00",
        "archived_at": archived_at,
        "parent_thread_id": None,
        "fork_point_turn_id": None,
    }


# ---------------------------------------------------------------------------
# helpers to get the stable user_id that the gateway derives for "owner"
# so ownership checks pass.
# ---------------------------------------------------------------------------

def _owner_user_id() -> int:
    """Mirror _stable_user_id("owner") from gateway.routes.chat."""
    import hashlib
    h = hashlib.md5("owner".encode()).hexdigest()
    return int(h[:8], 16)


# ---------------------------------------------------------------------------
# 1. rename_thread — happy path
# ---------------------------------------------------------------------------


def test_rename_thread_sets_title(tmp_path: Path) -> None:
    """PATCH /v1/chat/terry/threads/{id} with a valid title returns 200
    and title_locked is reflected in a subsequent list_threads call."""
    thread_id = "t-abc123"
    user_id = _owner_user_id()

    fake_vc = MagicMock()
    fake_vc.get_thread.return_value = _make_thread_meta(
        thread_id, user_id=user_id,
    )
    fake_vc.thread_rename = AsyncMock(return_value={"ok": True})
    fake_vc.list_threads.return_value = [
        _make_thread_meta(thread_id, user_id=user_id, title_locked=1),
    ]

    client, token = _build_client(tmp_path, vault_client=fake_vc)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.patch(
        f"/v1/chat/terry/threads/{thread_id}",
        json={"title": "My Shiny Title"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["id"] == thread_id
    assert body["title"] == "My Shiny Title"

    # Verify thread_rename was called with the right args.
    fake_vc.thread_rename.assert_awaited_once_with(
        thread_id=thread_id, title="My Shiny Title",
    )

    # Verify that a subsequent listing shows title_locked=True.
    r2 = client.get("/v1/chat/terry/threads", headers=headers)
    assert r2.status_code == 200, r2.text
    threads = r2.json()["threads"]
    assert threads[0]["title_locked"] == 1


# ---------------------------------------------------------------------------
# 2. rename_thread — rejects empty / whitespace title
# ---------------------------------------------------------------------------


def test_rename_rejects_empty_title(tmp_path: Path) -> None:
    """PATCH with empty title string → 400."""
    client, token = _build_client(tmp_path, vault_client=MagicMock())
    headers = {"Authorization": f"Bearer {token}"}

    for bad_title in ("", "   ", "\t\n"):
        r = client.patch(
            "/v1/chat/terry/threads/some-thread",
            json={"title": bad_title},
            headers=headers,
        )
        assert r.status_code == 400, f"Expected 400 for title={bad_title!r}, got {r.status_code}"


def test_rename_rejects_missing_title_field(tmp_path: Path) -> None:
    """PATCH with payload lacking 'title' key → 400."""
    client, token = _build_client(tmp_path, vault_client=MagicMock())
    headers = {"Authorization": f"Bearer {token}"}

    r = client.patch(
        "/v1/chat/terry/threads/some-thread",
        json={"other_field": "value"},
        headers=headers,
    )
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# 3. rename_thread — rejects wrong owner
# ---------------------------------------------------------------------------


def test_rename_rejects_other_owner(tmp_path: Path) -> None:
    """PATCH on a thread owned by a different user_id → 404."""
    thread_id = "t-other"
    # Thread belongs to user_id=9999, but our device maps to _owner_user_id().
    fake_vc = MagicMock()
    fake_vc.get_thread.return_value = _make_thread_meta(
        thread_id, user_id=9999,  # different owner
    )

    client, token = _build_client(tmp_path, vault_client=fake_vc)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.patch(
        f"/v1/chat/terry/threads/{thread_id}",
        json={"title": "Hijack Title"},
        headers=headers,
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# 4. unarchive_thread — clears archived_at
# ---------------------------------------------------------------------------


def test_unarchive_clears_archived_at(tmp_path: Path) -> None:
    """POST /v1/chat/terry/threads/{id}/unarchive → 200, unarchive was called."""
    thread_id = "t-archived"
    user_id = _owner_user_id()

    fake_vc = MagicMock()
    fake_vc.get_thread.return_value = _make_thread_meta(
        thread_id, user_id=user_id,
        archived_at="2026-01-15T10:00:00",
    )
    fake_vc.thread_unarchive = AsyncMock(return_value={"ok": True})

    client, token = _build_client(tmp_path, vault_client=fake_vc)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post(
        f"/v1/chat/terry/threads/{thread_id}/unarchive",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["id"] == thread_id

    fake_vc.thread_unarchive.assert_awaited_once_with(thread_id=thread_id)


# ---------------------------------------------------------------------------
# 5. pin_thread — toggle
# ---------------------------------------------------------------------------


def test_pin_toggle(tmp_path: Path) -> None:
    """POST /v1/chat/terry/threads/{id}/pin with pinned=true → 200 + pinned:true."""
    thread_id = "t-pintest"
    user_id = _owner_user_id()

    fake_vc = MagicMock()
    fake_vc.get_thread.return_value = _make_thread_meta(
        thread_id, user_id=user_id,
    )
    fake_vc.thread_pin = AsyncMock(return_value={"ok": True})

    client, token = _build_client(tmp_path, vault_client=fake_vc)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post(
        f"/v1/chat/terry/threads/{thread_id}/pin",
        json={"pinned": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["id"] == thread_id
    assert body["pinned"] is True

    fake_vc.thread_pin.assert_awaited_once_with(
        thread_id=thread_id, pinned=True,
    )


def test_pin_unpin_toggle(tmp_path: Path) -> None:
    """POST /v1/chat/terry/threads/{id}/pin with pinned=false unpins."""
    thread_id = "t-unpin"
    user_id = _owner_user_id()

    fake_vc = MagicMock()
    fake_vc.get_thread.return_value = _make_thread_meta(
        thread_id, user_id=user_id, pinned=1,
    )
    fake_vc.thread_pin = AsyncMock(return_value={"ok": True})

    client, token = _build_client(tmp_path, vault_client=fake_vc)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post(
        f"/v1/chat/terry/threads/{thread_id}/pin",
        json={"pinned": False},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pinned"] is False

    fake_vc.thread_pin.assert_awaited_once_with(
        thread_id=thread_id, pinned=False,
    )


def test_pin_defaults_to_true_when_no_payload(tmp_path: Path) -> None:
    """POST pin with no body defaults pinned to True."""
    thread_id = "t-nopin"
    user_id = _owner_user_id()

    fake_vc = MagicMock()
    fake_vc.get_thread.return_value = _make_thread_meta(
        thread_id, user_id=user_id,
    )
    fake_vc.thread_pin = AsyncMock(return_value={"ok": True})

    client, token = _build_client(tmp_path, vault_client=fake_vc)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post(
        f"/v1/chat/terry/threads/{thread_id}/pin",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pinned"] is True


# ---------------------------------------------------------------------------
# 6. search_threads — GET /v1/chat/{bot}/threads/search?q=…
# ---------------------------------------------------------------------------


def test_search_threads_returns_hits(tmp_path: Path) -> None:
    """GET /v1/chat/terry/threads/search?q=kraken returns matching thread."""
    user_id = _owner_user_id()

    fake_vc = MagicMock()
    fake_vc.search_threads.return_value = [
        {
            "thread": _make_thread_meta("t-k1", user_id=user_id),
            "snippet": "we discussed the [kraken] at length",
        }
    ]

    client, token = _build_client(tmp_path, vault_client=fake_vc)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get(
        "/v1/chat/terry/threads/search",
        params={"q": "kraken", "limit": 10},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "hits" in body
    assert len(body["hits"]) == 1
    hit = body["hits"][0]
    assert hit["thread"]["id"] == "t-k1"
    assert "kraken" in hit["snippet"].lower()

    fake_vc.search_threads.assert_called_once_with(
        bot="terry", user_id=user_id, query="kraken", limit=10,
    )


def test_search_threads_unknown_bot_returns_404(tmp_path: Path) -> None:
    """GET /v1/chat/unknown-bot/threads/search → 404."""
    client, token = _build_client(tmp_path, vault_client=MagicMock())
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get(
        "/v1/chat/unknown-bot/threads/search",
        params={"q": "kraken"},
        headers=headers,
    )
    assert r.status_code == 404, r.text


def test_search_threads_limit_clamped(tmp_path: Path) -> None:
    """limit param is clamped to [1, 100]."""
    user_id = _owner_user_id()

    fake_vc = MagicMock()
    fake_vc.search_threads.return_value = []

    client, token = _build_client(tmp_path, vault_client=fake_vc)
    headers = {"Authorization": f"Bearer {token}"}

    # Request limit=999 → should be clamped to 100
    r = client.get(
        "/v1/chat/terry/threads/search",
        params={"q": "anything", "limit": 999},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    fake_vc.search_threads.assert_called_once_with(
        bot="terry", user_id=user_id, query="anything", limit=100,
    )
