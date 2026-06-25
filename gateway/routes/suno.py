"""Suno music library routes.

GET /v1/suno/tracks  — JSON track list from the Suno library SQLite db.
                       Open (no auth), loopback/tailnet only.
GET /v1/suno/audio/{id} — Range-aware MP3 stream from file_path on disk.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from gateway.config import Config

router = APIRouter(prefix="/v1/suno", tags=["suno"])
log = logging.getLogger("gateway.suno")

# ── constants ─────────────────────────────────────────────────────────────────

_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_CHUNK = 256 * 1024  # 256 KB streaming chunk


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_config(request: Request) -> Config:
    return request.app.state.ai_team.config


def _suno_root() -> Path:
    """Optional Suno integration root. Defaults under the projects root
    (HIVE_PROJECTS_ROOT); set suno_library_db / suno_downloads_dir in config to
    point at a real suno-music-downloader install. Missing → routes return empty."""
    base = Path(
        os.environ.get("HIVE_PROJECTS_ROOT", str(Path.home() / "projects"))
    ).expanduser()
    return base / "suno-music-downloader"


def _db_path(config: Config) -> Path:
    raw = getattr(config, "suno_library_db", None)
    if raw:
        return Path(raw)
    return _suno_root() / "library.db"


def _downloads_dir(config: Config) -> Path:
    raw = getattr(config, "suno_downloads_dir", None)
    if raw:
        return Path(raw)
    return _suno_root() / "downloads"


def _open_db(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as exc:  # noqa: BLE001
        log.warning("suno: cannot open db %s: %s", db_path, exc)
        return None


# ── routes ────────────────────────────────────────────────────────────────────

def _display_title(row: sqlite3.Row) -> str:
    """A human-readable title for a track.

    Most rows have a real title ("Velvet Crown (Deathcore)"). A minority are
    bare audio-only downloads whose `title` is just the track UUID (no tags or
    prompt to derive from). Surface those as "Untitled · <short-id>" so the
    player never shows a raw UUID and the bare tracks stay distinguishable.
    """
    t = (row["title"] or "").strip()
    rid = row["id"]
    if t and t != rid and not _ID_RE.match(t):
        return t
    short = rid.split("-", 1)[0] if rid else "?"
    return f"Untitled · {short}"


@router.get("/tracks")
def list_tracks(request: Request) -> Any:
    """Return track metadata, named tracks first then untitled, newest within each.

    Returns an empty list if the library DB is unavailable (graceful degradation).
    """
    config = _get_config(request)
    db_path = _db_path(config)
    conn = _open_db(db_path)
    if conn is None:
        log.info("suno: library db not found at %s — returning empty list", db_path)
        return []

    try:
        rows = conn.execute(
            """
            SELECT id, title, artist_name, tags, duration, image_url, play_count
            FROM tracks
            ORDER BY created_at DESC
            """,
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["title"] = _display_title(r)
            out.append(d)
        # Real titles first, the bare "Untitled · …" tracks last. Stable sort
        # keeps the created_at-desc order within each group.
        out.sort(key=lambda d: d["title"].startswith("Untitled · "))
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("suno: query failed: %s", exc)
        return []
    finally:
        conn.close()


@router.get("/audio/{track_id}")
def stream_audio(track_id: str, request: Request) -> Response:
    """Stream the MP3 for the given track ID.

    Supports HTTP Range requests so scrubbing/seeking works.
    Validates the track ID and resolves the file path from the DB.
    Returns 404 if the track or file is missing.
    Path-traversal guard: resolved path must be inside the downloads dir.
    """
    # Validate ID format to prevent SQL injection vectors (belt + suspenders).
    if not _ID_RE.match(track_id):
        raise HTTPException(status_code=404, detail="track not found")

    config = _get_config(request)
    db_path = _db_path(config)
    downloads_dir = _downloads_dir(config)

    conn = _open_db(db_path)
    if conn is None:
        raise HTTPException(status_code=503, detail="suno library unavailable")

    try:
        row = conn.execute(
            "SELECT file_path FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="track not found")

    raw_path = row["file_path"]
    if not raw_path:
        raise HTTPException(status_code=404, detail="track has no file")

    file_path = Path(raw_path).resolve()

    # Path-traversal guard: resolved path must be inside the downloads dir.
    downloads_resolved = downloads_dir.resolve()
    try:
        file_path.relative_to(downloads_resolved)
    except ValueError:
        log.warning(
            "suno: path traversal attempt — id=%s resolved_path=%s",
            track_id, file_path,
        )
        raise HTTPException(status_code=404, detail="track not found")

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="audio file not found on disk")

    file_size = file_path.stat().st_size
    content_type = mimetypes.guess_type(str(file_path))[0] or "audio/mpeg"

    range_header = request.headers.get("Range")
    if not range_header:
        # Full file response.
        return FileResponse(
            str(file_path),
            media_type=content_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
            },
        )

    # Parse Range: bytes=start-end
    range_match = re.match(r"bytes=(\d*)-(\d*)", range_header)
    if not range_match:
        raise HTTPException(status_code=416, detail="invalid range header")

    start_str, end_str = range_match.group(1), range_match.group(2)
    start = int(start_str) if start_str else 0
    end   = int(end_str)   if end_str   else file_size - 1

    if start > end or end >= file_size:
        raise HTTPException(
            status_code=416,
            detail="requested range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    content_length = end - start + 1

    def _iter_range():
        with file_path.open("rb") as fh:
            fh.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk = fh.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        _iter_range(),
        status_code=206,
        media_type=content_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
        },
    )
