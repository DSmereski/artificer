"""Tests for the /v1/music/* routes (F4.1 gateway backend).

Tests:
  - Scanning a temp folder of fake audio files returns tracks.
  - A Range request returns 206 with the correct bytes.
  - A path-traversal attempt (../../etc) is rejected with 4xx.
  - Missing-mutagen path: scan still works using filename heuristics.
"""

from __future__ import annotations

import builtins
from pathlib import Path

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
from gateway.music_library import PathSandbox, TrackRegistry


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path) -> Config:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    return Config(
        bind_host="127.0.0.1",
        bind_port=0,
        tailscale_bind=None,
        state_dir=state,
        vault_writer=VaultWriterConfig(
            host="127.0.0.1",
            port=8765,
            token_path=tmp_path / "no-token",
        ),
        vault_path=vault,
        history_roots={},
        models={},
        pairing=PairingConfig(code_ttl_seconds=60, code_length=8, token_bytes=16),
        ntfy=NtfyConfig(base_url="http://127.0.0.1:8080", enabled=False),
        rate_limits=RateLimits(writes_per_minute=60, images_per_hour=30),
    )


def _make_fake_audio(path: Path, size: int = 4096) -> None:
    """Write a tiny fake audio file with a recognisable header + body."""
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\xAB" * size)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def music_env(tmp_path: Path, monkeypatch):
    """Set up a temporary music folder with 2 fake audio files.

    Patches ``gateway.routes.music.make_sandbox`` so the sandbox is rooted
    at the temp dir (not the real ``%USERPROFILE%\\Music``).

    Yields (client, music_root, f1, f2).
    """
    import gateway.routes.music as music_mod
    import gateway.music_library as lib_mod

    music_root = tmp_path / "music"
    music_root.mkdir()

    # Create two fake audio files.
    f1 = music_root / "01 - Track One.mp3"
    f2 = music_root / "02 - Track Two.flac"
    _make_fake_audio(f1, size=8192)
    _make_fake_audio(f2, size=4096)

    # Reset module-level singletons so this test starts clean.
    music_mod._sandbox = None
    music_mod._folder_store = None
    music_mod._track_registry = TrackRegistry()

    # Patch make_sandbox in the route's namespace so _get_sandbox() uses
    # our temp root instead of %USERPROFILE%\Music.
    def _sandbox_for_test(extra_roots=None):
        roots = [music_root]
        if extra_roots:
            roots.extend(extra_roots)
        return PathSandbox(roots)

    monkeypatch.setattr(music_mod, "make_sandbox", _sandbox_for_test)

    config = _make_config(tmp_path)
    app = create_app(config)
    client = TestClient(app)

    yield client, music_root, f1, f2

    # Cleanup singletons after each test.
    music_mod._sandbox = None
    music_mod._folder_store = None
    music_mod._track_registry = TrackRegistry()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _index_tracks(client: TestClient, folder: str) -> list[dict]:
    resp = client.get("/v1/music/tracks", params={"folder": folder})
    assert resp.status_code == 200, resp.text
    return resp.json()["tracks"]


# ---------------------------------------------------------------------------
# Tests: scanning
# ---------------------------------------------------------------------------

class TestScan:
    def test_scan_returns_two_tracks(self, music_env):
        client, music_root, f1, f2 = music_env
        tracks = _index_tracks(client, str(music_root))
        assert len(tracks) == 2

    def test_track_dict_has_required_keys(self, music_env):
        client, music_root, f1, f2 = music_env
        tracks = _index_tracks(client, str(music_root))
        required = {"id", "path", "title", "artist", "album", "duration_s", "track_no"}
        for t in tracks:
            assert required <= set(t.keys()), f"missing keys in {t}"

    def test_track_id_is_stable(self, music_env):
        client, music_root, f1, f2 = music_env
        tracks1 = _index_tracks(client, str(music_root))
        tracks2 = _index_tracks(client, str(music_root))
        ids1 = {t["id"] for t in tracks1}
        ids2 = {t["id"] for t in tracks2}
        assert ids1 == ids2, "track IDs must be stable across calls"

    def test_title_extracted_from_filename(self, music_env):
        """Even without mutagen tags, title is derived from the filename."""
        client, music_root, f1, f2 = music_env
        tracks = _index_tracks(client, str(music_root))
        titles = {t["title"] for t in tracks}
        # "01 - Track One.mp3" → heuristic strips "01 - " → "Track One"
        assert any("Track One" in title for title in titles), f"titles: {titles}"

    def test_empty_folder_returns_empty_list(self, music_env):
        client, music_root, f1, f2 = music_env
        empty = music_root / "empty"
        empty.mkdir()
        tracks = _index_tracks(client, str(empty))
        assert tracks == []

    def test_scan_outside_sandbox_returns_400(self, music_env, tmp_path):
        client, music_root, f1, f2 = music_env
        # tmp_path is parent of music_root — outside the sandbox.
        resp = client.get("/v1/music/tracks", params={"folder": str(tmp_path)})
        assert resp.status_code == 400

    def test_non_audio_files_skipped(self, music_env):
        client, music_root, f1, f2 = music_env
        (music_root / "readme.txt").write_text("not audio")
        tracks = _index_tracks(client, str(music_root))
        paths = [t["path"] for t in tracks]
        assert all(not p.endswith(".txt") for p in paths)


# ---------------------------------------------------------------------------
# Tests: Range streaming
# ---------------------------------------------------------------------------

class TestRangeStream:
    def _get_mp3_track(self, music_env) -> dict:
        client, music_root, f1, f2 = music_env
        tracks = _index_tracks(client, str(music_root))
        return next(t for t in tracks if t["path"].endswith(".mp3"))

    def test_range_request_returns_206(self, music_env):
        client, music_root, f1, f2 = music_env
        track = self._get_mp3_track(music_env)
        resp = client.get(
            f"/v1/music/stream/{track['id']}",
            headers={"Range": "bytes=0-99"},
        )
        assert resp.status_code == 206, resp.text

    def test_range_content_range_header(self, music_env):
        client, music_root, f1, f2 = music_env
        track = self._get_mp3_track(music_env)
        resp = client.get(
            f"/v1/music/stream/{track['id']}",
            headers={"Range": "bytes=0-99"},
        )
        assert resp.status_code == 206
        assert resp.headers.get("content-range", "").startswith("bytes 0-99/")

    def test_range_body_length(self, music_env):
        client, music_root, f1, f2 = music_env
        track = self._get_mp3_track(music_env)
        resp = client.get(
            f"/v1/music/stream/{track['id']}",
            headers={"Range": "bytes=0-99"},
        )
        assert resp.status_code == 206
        assert len(resp.content) == 100

    def test_range_correct_bytes(self, music_env):
        """The bytes in the Range response must match the actual file bytes."""
        client, music_root, f1, f2 = music_env
        track = self._get_mp3_track(music_env)
        # File: 4-byte header + 8192 bytes of 0xAB. Request bytes 4-103.
        resp = client.get(
            f"/v1/music/stream/{track['id']}",
            headers={"Range": "bytes=4-103"},
        )
        assert resp.status_code == 206
        assert resp.content == b"\xAB" * 100

    def test_full_response_200_with_accept_ranges(self, music_env):
        client, music_root, f1, f2 = music_env
        track = self._get_mp3_track(music_env)
        resp = client.get(f"/v1/music/stream/{track['id']}")
        assert resp.status_code == 200
        assert resp.headers.get("accept-ranges") == "bytes"

    def test_invalid_range_returns_416(self, music_env):
        client, music_root, f1, f2 = music_env
        track = self._get_mp3_track(music_env)
        # File is 8196 bytes; request way beyond end.
        resp = client.get(
            f"/v1/music/stream/{track['id']}",
            headers={"Range": "bytes=99999-999999"},
        )
        assert resp.status_code == 416

    def test_unknown_id_returns_404(self, music_env):
        client, music_root, f1, f2 = music_env
        _index_tracks(client, str(music_root))
        resp = client.get("/v1/music/stream/deadbeef00000000")
        assert resp.status_code == 404

    def test_content_type_for_mp3(self, music_env):
        client, music_root, f1, f2 = music_env
        track = self._get_mp3_track(music_env)
        resp = client.get(f"/v1/music/stream/{track['id']}")
        assert "audio" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Tests: path traversal / sandbox
# ---------------------------------------------------------------------------

class TestSandbox:
    def test_browse_outside_roots_returns_400(self, music_env, tmp_path):
        client, music_root, f1, f2 = music_env
        outside = tmp_path / "outside"
        outside.mkdir()
        resp = client.get("/v1/music/browse", params={"path": str(outside)})
        assert resp.status_code == 400

    def test_tracks_traversal_attempt_returns_400(self, music_env, tmp_path):
        client, music_root, f1, f2 = music_env
        # Classic traversal: music_root/../.. resolves to grandparent, outside sandbox.
        evil_path = str(music_root / ".." / "..")
        resp = client.get("/v1/music/tracks", params={"folder": evil_path})
        assert resp.status_code == 400

    def test_stream_traversal_id_outside_sandbox_is_blocked(self, music_env, tmp_path):
        """An ID that maps to a file outside the sandbox must return 404."""
        import gateway.routes.music as music_mod
        import gateway.music_library as lib_mod

        client, music_root, f1, f2 = music_env

        # Plant a file outside the sandbox.
        secret = tmp_path / "secret.mp3"
        secret.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 100)

        # Manually inject the outside-sandbox path into the track registry.
        evil_id = lib_mod.track_id_for_path(secret)
        music_mod._track_registry._map[evil_id] = str(secret)

        resp = client.get(f"/v1/music/stream/{evil_id}")
        assert resp.status_code == 404

    def test_browse_no_path_returns_roots(self, music_env):
        """GET /v1/music/browse with no path returns sandbox roots."""
        client, music_root, f1, f2 = music_env
        resp = client.get("/v1/music/browse")
        assert resp.status_code == 200
        data = resp.json()
        assert "dirs" in data

    def test_browse_inside_music_root_works(self, music_env):
        client, music_root, f1, f2 = music_env
        sub = music_root / "sub"
        sub.mkdir()
        resp = client.get("/v1/music/browse", params={"path": str(music_root)})
        assert resp.status_code == 200
        dirs = resp.json()["dirs"]
        names = [d["name"] for d in dirs]
        assert "sub" in names


# ---------------------------------------------------------------------------
# Tests: mutagen-optional (filename fallback)
# ---------------------------------------------------------------------------

class TestMutagenOptional:
    def test_scan_works_without_mutagen(self, music_env, monkeypatch):
        """Scanning must succeed even when mutagen is unavailable."""
        import gateway.music_library as lib_mod
        import gateway.routes.music as music_mod

        monkeypatch.setattr(lib_mod, "_MUTAGEN_AVAILABLE", False)
        monkeypatch.setattr(lib_mod, "_MutagenFile", None)

        # Force sandbox rebuild so any cached mutagen-based result is gone.
        music_mod._sandbox = None

        client, music_root, f1, f2 = music_env
        tracks = _index_tracks(client, str(music_root))
        assert len(tracks) == 2
        for t in tracks:
            assert t["title"], f"title must be non-empty: {t}"
            assert t["id"],    f"id must be non-empty: {t}"

    def test_title_heuristic_strips_track_number(self, music_env, monkeypatch):
        import gateway.music_library as lib_mod
        import gateway.routes.music as music_mod

        monkeypatch.setattr(lib_mod, "_MUTAGEN_AVAILABLE", False)
        monkeypatch.setattr(lib_mod, "_MutagenFile", None)
        music_mod._sandbox = None

        client, music_root, f1, f2 = music_env
        tracks = _index_tracks(client, str(music_root))
        titles = {t["title"] for t in tracks}
        # "01 - Track One.mp3" → "Track One"
        assert any("Track One" in t for t in titles), f"titles: {titles}"

    def test_art_returns_404_when_no_embedded_art(self, music_env):
        """Our fake mp3s have no embedded art; /art/ must return 404."""
        client, music_root, f1, f2 = music_env
        tracks = _index_tracks(client, str(music_root))
        mp3 = next(t for t in tracks if t["path"].endswith(".mp3"))
        resp = client.get(f"/v1/music/art/{mp3['id']}")
        # Either 404 (no art extracted) or 404 (mutagen import blocked).
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: folders endpoints
# ---------------------------------------------------------------------------

class TestFolders:
    def test_list_folders_initially_empty(self, music_env):
        client, music_root, f1, f2 = music_env
        resp = client.get("/v1/music/folders")
        assert resp.status_code == 200
        data = resp.json()
        assert "folders" in data

    def test_add_folder_persists(self, music_env):
        client, music_root, f1, f2 = music_env
        resp = client.post("/v1/music/folders", json={"path": str(music_root)})
        assert resp.status_code == 200
        data = resp.json()
        # The resolved path should be in the list.
        folders = data["folders"]
        assert any(str(music_root.resolve()) in f or str(music_root) in f for f in folders)

    def test_add_nonexistent_folder_returns_400(self, music_env, tmp_path):
        client, music_root, f1, f2 = music_env
        resp = client.post(
            "/v1/music/folders",
            json={"path": str(tmp_path / "does_not_exist")},
        )
        assert resp.status_code == 400

    def test_list_folders_after_add(self, music_env):
        client, music_root, f1, f2 = music_env
        client.post("/v1/music/folders", json={"path": str(music_root)})
        resp = client.get("/v1/music/folders")
        assert resp.status_code == 200
        folders = resp.json()["folders"]
        assert len(folders) >= 1
