"""Tests for GET /v1/suno/tracks and GET /v1/suno/audio/{id}."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.config import Config, NtfyConfig, PairingConfig, RateLimits, VaultWriterConfig


# ── fixtures ──────────────────────────────────────────────────────────────────

def _make_config(tmp_path: Path, db_path: Path | None = None, downloads_dir: Path | None = None) -> Config:
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
        suno_library_db=str(db_path) if db_path else str(tmp_path / "nonexistent.db"),
        suno_downloads_dir=str(downloads_dir) if downloads_dir else str(tmp_path / "no-downloads"),
    )


def _seed_db(db_path: Path, downloads_dir: Path) -> None:
    """Create a minimal library.db and a tiny fake mp3 for testing."""
    downloads_dir.mkdir(parents=True, exist_ok=True)
    mp3 = downloads_dir / "test-track [aabbccdd-1122-3344-5566-778899aabbcc].mp3"
    # Write a minimal fake mp3 (just enough bytes to support range requests).
    mp3.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 4096)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE tracks (
            id TEXT PRIMARY KEY,
            title TEXT,
            tags TEXT,
            prompt TEXT,
            duration REAL,
            file_path TEXT,
            file_format TEXT,
            file_size INTEGER,
            image_url TEXT,
            artist_handle TEXT,
            artist_name TEXT,
            artist_avatar TEXT,
            model_name TEXT,
            play_count INTEGER,
            last_played_at REAL,
            created_at REAL,
            updated_at REAL,
            deleted_at REAL
        )"""
    )
    conn.execute(
        "INSERT INTO tracks (id, title, artist_name, tags, duration, image_url, play_count, file_path, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "aabbccdd-1122-3344-5566-778899aabbcc",
            "Test Track",
            "Test Artist",
            "electronic pop",
            180.0,
            "https://cdn.suno.ai/image_test.jpeg",
            3,
            str(mp3),
            1700000000.0,
        ),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def suno_setup(tmp_path: Path):
    """Returns (client, downloads_dir, track_id) for a fully seeded test env."""
    db_path = tmp_path / "library.db"
    downloads_dir = tmp_path / "downloads"
    _seed_db(db_path, downloads_dir)
    config = _make_config(tmp_path, db_path=db_path, downloads_dir=downloads_dir)
    app = create_app(config)
    client = TestClient(app)
    return client, downloads_dir, "aabbccdd-1122-3344-5566-778899aabbcc"


@pytest.fixture
def missing_db_client(tmp_path: Path):
    """Returns a client configured with a non-existent db."""
    config = _make_config(tmp_path)
    app = create_app(config)
    return TestClient(app)


# ── /v1/suno/tracks ───────────────────────────────────────────────────────────

class TestTrackList:
    def test_returns_track_list(self, suno_setup):
        client, _, track_id = suno_setup
        resp = client.get("/v1/suno/tracks")
        assert resp.status_code == 200
        tracks = resp.json()
        assert isinstance(tracks, list)
        assert len(tracks) == 1
        t = tracks[0]
        assert t["id"] == track_id
        assert t["title"] == "Test Track"
        assert t["artist_name"] == "Test Artist"
        assert t["duration"] == 180.0

    def test_missing_db_returns_empty_list(self, missing_db_client):
        resp = missing_db_client.get("/v1/suno/tracks")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_no_auth_required(self, suno_setup):
        client, _, _ = suno_setup
        # No Authorization header — should still succeed (open endpoint).
        resp = client.get("/v1/suno/tracks")
        assert resp.status_code == 200


# ── /v1/suno/audio/{id} ───────────────────────────────────────────────────────

class TestAudioStream:
    def test_full_response_200(self, suno_setup):
        client, _, track_id = suno_setup
        resp = client.get(f"/v1/suno/audio/{track_id}")
        assert resp.status_code == 200
        assert "audio" in resp.headers.get("content-type", "").lower()

    def test_range_request_returns_206(self, suno_setup):
        client, downloads_dir, track_id = suno_setup
        # The fake mp3 is 4100 bytes; request first 100 bytes.
        resp = client.get(
            f"/v1/suno/audio/{track_id}",
            headers={"Range": "bytes=0-99"},
        )
        assert resp.status_code == 206
        assert resp.headers.get("content-range", "").startswith("bytes 0-99/")
        assert resp.headers.get("content-length") == "100"
        assert len(resp.content) == 100

    def test_range_mid_file(self, suno_setup):
        client, _, track_id = suno_setup
        resp = client.get(
            f"/v1/suno/audio/{track_id}",
            headers={"Range": "bytes=100-199"},
        )
        assert resp.status_code == 206
        assert "100-199" in resp.headers.get("content-range", "")

    def test_bad_id_returns_404(self, suno_setup):
        client, _, _ = suno_setup
        resp = client.get("/v1/suno/audio/nonexistent-track-id")
        assert resp.status_code == 404

    def test_unknown_uuid_returns_404(self, suno_setup):
        client, _, _ = suno_setup
        resp = client.get("/v1/suno/audio/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_path_traversal_blocked(self, suno_setup, tmp_path):
        client, downloads_dir, track_id = suno_setup
        # Write a secret file outside the downloads dir.
        secret = tmp_path / "secret.mp3"
        secret.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 100)

        # Patch the track's file_path to point outside downloads_dir via sqlite.
        db_path = tmp_path / "library.db"
        conn = sqlite3.connect(str(db_path))
        evil_id = "ddccbbaa-2211-4433-6655-aabbcc998877"
        conn.execute(
            "INSERT INTO tracks (id, title, artist_name, file_path, created_at) VALUES (?, ?, ?, ?, ?)",
            (evil_id, "Evil", "Evil", str(secret), 0.0),
        )
        conn.commit()
        conn.close()

        resp = client.get(f"/v1/suno/audio/{evil_id}")
        assert resp.status_code == 404

    def test_missing_db_returns_503(self, missing_db_client):
        resp = missing_db_client.get("/v1/suno/audio/aabbccdd-1122-3344-5566-778899aabbcc")
        assert resp.status_code == 503
