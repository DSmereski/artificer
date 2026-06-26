"""Local music library routes.

GET  /v1/music/browse?path=<dir>      List immediate subdirectories (mouse navigation).
GET  /v1/music/folders                List remembered library folders.
POST /v1/music/folders                Add a library folder {path}.
GET  /v1/music/tracks?folder=<path>   Scan folder, return track list.
GET  /v1/music/stream/{track_id}      Range-aware audio stream.
GET  /v1/music/art/{track_id}         Embedded cover art (if available).

All path operations are sandboxed to allowed roots.  No auth required for
reads (same convention as suno, board-read-only): the gateway binds loopback
/ tailnet only so open GET endpoints are fine.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from gateway.music_library import (
    LibraryFolderStore,
    PathSandbox,
    TrackRegistry,
    content_type_for,
    list_subdirectories,
    make_sandbox,
    scan_folder,
    track_id_for_path,
)

router = APIRouter(prefix="/v1/music", tags=["music"])
log = logging.getLogger("gateway.music")

_CHUNK = 256 * 1024  # 256 KB per streaming chunk

# ---------------------------------------------------------------------------
# Module-level shared state (re-used across requests in one gateway process)
# ---------------------------------------------------------------------------

_sandbox: PathSandbox | None = None
_track_registry: TrackRegistry = TrackRegistry()
_folder_store: LibraryFolderStore | None = None


def _get_sandbox(request: Request) -> PathSandbox:
    global _sandbox, _folder_store  # noqa: PLW0603
    if _sandbox is None:
        # Build sandbox from the default music root + any persisted folders.
        extra: list[Path] = []
        if _folder_store is not None:
            extra = [Path(p) for p in _folder_store.all() if Path(p).is_dir()]
        _sandbox = make_sandbox(extra)
        # Also add persisted folders to the sandbox roots directly.
        for p in extra:
            _sandbox.add_root(p)
    return _sandbox


def _get_folder_store(request: Request) -> LibraryFolderStore:
    global _folder_store, _sandbox  # noqa: PLW0603
    if _folder_store is None:
        state_dir: Path = request.app.state.ai_team.config.state_dir
        store_path = state_dir / "music_folders.json"
        _folder_store = LibraryFolderStore(store_path)
    return _folder_store


# ---------------------------------------------------------------------------
# GET /v1/music/browse
# ---------------------------------------------------------------------------

@router.get("/browse")
def browse_directory(request: Request, path: str | None = None) -> JSONResponse:
    """List immediate subdirectories of *path*, or the allowed roots if no path.

    Mouse-only navigation: the client calls this to let the user click into
    folders without needing to type paths.
    """
    sandbox = _get_sandbox(request)
    folder_store = _get_folder_store(request)

    if path is None:
        # Return all allowed roots as the top-level listing.
        roots = sandbox.roots
        dirs = [{"name": r.name or str(r), "path": str(r)} for r in roots if r.is_dir()]
        # Also include the default Music folder if it exists.
        return JSONResponse({"dirs": dirs, "current": None})

    try:
        dirs = list_subdirectories(Path(path), sandbox)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return JSONResponse({"dirs": dirs, "current": path})


# ---------------------------------------------------------------------------
# GET /v1/music/folders
# ---------------------------------------------------------------------------

@router.get("/folders")
def list_folders(request: Request) -> JSONResponse:
    """Return the list of remembered library folder paths."""
    store = _get_folder_store(request)
    return JSONResponse({"folders": store.all()})


# ---------------------------------------------------------------------------
# POST /v1/music/folders
# ---------------------------------------------------------------------------

@router.post("/folders")
def add_folder(request: Request, path: str = Body(..., embed=True)) -> JSONResponse:
    """Remember a new library folder.

    The path must exist and must be inside the allow-listed roots (or become
    a new root if it expands the sandbox; we add it to the sandbox too).
    """
    global _sandbox  # noqa: PLW0603

    resolved = Path(path).resolve()
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="path is not a directory")

    store = _get_folder_store(request)
    sandbox = _get_sandbox(request)

    # We allow the user to add any real directory as a new root.
    # This expands the sandbox — add it to both the sandbox and the store.
    sandbox.add_root(resolved)
    store.add(str(resolved))

    # Invalidate cached sandbox so the next _get_sandbox() rebuilds with
    # the new root in scope.
    _sandbox = None

    return JSONResponse({"folders": store.all(), "added": str(resolved)})


# ---------------------------------------------------------------------------
# GET /v1/music/tracks
# ---------------------------------------------------------------------------

@router.get("/tracks")
def list_tracks(request: Request, folder: str) -> JSONResponse:
    """Scan *folder* and return a list of track dicts.

    Each track: {id, path, title, artist, album, duration_s, track_no}.
    The track *id* is stable across calls for the same file.
    """
    sandbox = _get_sandbox(request)

    try:
        tracks = scan_folder(Path(folder), sandbox)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Keep the registry warm so stream/{id} can resolve without re-scanning.
    _track_registry.index(tracks)

    return JSONResponse({"folder": folder, "tracks": tracks})


# ---------------------------------------------------------------------------
# GET /v1/music/stream/{track_id}
# ---------------------------------------------------------------------------

def _resolve_track(track_id: str, sandbox: PathSandbox) -> Path:
    """Resolve a track_id to a safe file path.

    First checks the in-process registry (fast); the client is expected to
    call /tracks first which populates it.  Returns 404 on miss.
    """
    # Validate format: 16 hex chars.
    if not re.fullmatch(r"[0-9a-f]{16}", track_id):
        raise HTTPException(status_code=404, detail="track not found")

    path = _track_registry.resolve(track_id)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail="track not found — call /v1/music/tracks first to index the folder",
        )

    # Double-check sandbox (registry could theoretically drift).
    try:
        safe = sandbox.resolve_safe(path)
    except ValueError:
        log.warning("music: sandboxed-out resolved path for id=%s path=%s", track_id, path)
        raise HTTPException(status_code=404, detail="track not found")

    if not safe.is_file():
        raise HTTPException(status_code=404, detail="audio file missing on disk")

    return safe


@router.get("/stream/{track_id}")
def stream_track(track_id: str, request: Request) -> Response:
    """Serve the audio file with full HTTP Range support.

    - No Range header: 200 with full content + Accept-Ranges header.
    - Range header: 206 Partial Content with correct Content-Range.

    This lets the HTML <audio> element seek/scrub without re-downloading
    the whole file.
    """
    sandbox = _get_sandbox(request)
    path = _resolve_track(track_id, sandbox)

    file_size = path.stat().st_size
    content_type = content_type_for(path)
    range_header = request.headers.get("Range")

    if not range_header:
        # Full response — still advertise Accept-Ranges so the client
        # knows it can seek.
        return StreamingResponse(
            _stream_file(path, 0, file_size),
            status_code=200,
            media_type=content_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
            },
        )

    # Parse "bytes=start-end"
    m = re.match(r"bytes=(\d*)-(\d*)", range_header)
    if not m:
        raise HTTPException(status_code=416, detail="invalid range header")

    start_str, end_str = m.group(1), m.group(2)
    start = int(start_str) if start_str else 0
    end   = int(end_str)   if end_str   else file_size - 1

    if start > end or end >= file_size:
        raise HTTPException(
            status_code=416,
            detail="requested range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    content_length = end - start + 1

    return StreamingResponse(
        _stream_file(path, start, content_length),
        status_code=206,
        media_type=content_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
        },
    )


def _stream_file(path: Path, offset: int, length: int):
    """Generator: yield at most *length* bytes from *path* starting at *offset*."""
    with path.open("rb") as fh:
        fh.seek(offset)
        remaining = length
        while remaining > 0:
            chunk = fh.read(min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


# ---------------------------------------------------------------------------
# GET /v1/music/art/{track_id}
# ---------------------------------------------------------------------------

@router.get("/art/{track_id}")
def get_cover_art(track_id: str, request: Request) -> Response:
    """Return embedded cover art if available, else 404.

    The client falls back to a generated placeholder on 404.
    Only attempted if mutagen is installed (otherwise always 404).
    """
    sandbox = _get_sandbox(request)
    path = _resolve_track(track_id, sandbox)

    art_data, mime = _extract_art(path)
    if art_data is None:
        raise HTTPException(status_code=404, detail="no embedded cover art")

    return Response(content=art_data, media_type=mime)


def _extract_art(path: Path) -> tuple[bytes | None, str]:
    """Return (image_bytes, mime) or (None, "") if unavailable."""
    try:
        import mutagen  # noqa: F401
        from mutagen import File as MuFile

        af = MuFile(str(path))
        if af is None:
            return None, ""

        # ID3 tags (mp3): APIC frame.
        if hasattr(af, "tags") and af.tags:
            for key in af.tags:
                if key.startswith("APIC"):
                    apic = af.tags[key]
                    return apic.data, apic.mime or "image/jpeg"

        # FLAC / Ogg: pictures attribute.
        pics = getattr(af, "pictures", None)
        if pics:
            pic = pics[0]
            return pic.data, pic.mime or "image/jpeg"

        # MP4 / M4A: covr atom.
        covr = af.get("covr") if hasattr(af, "get") else None
        if covr:
            img = covr[0]
            # mutagen MP4Cover: format 13 = JPEG, 14 = PNG.
            mime = "image/png" if getattr(img, "imageformat", 13) == 14 else "image/jpeg"
            return bytes(img), mime

    except Exception as exc:  # noqa: BLE001
        log.debug("art extraction failed for %s: %s", path, exc)

    return None, ""
