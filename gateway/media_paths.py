"""Shared media-path helpers for the gateway.

`resolve_uploaded_reference(state_dir, media_id)` returns the on-disk
path of a previously-uploaded reference image, or None if the id is
invalid / not present. Confined to `<state_dir>/media-uploads/<id>.<ext>`
with `<id>` alphanum + `<= 64` chars, so an LLM-emitted media_id can't
escape the upload dir.

Lives here (not in `routes/images.py`) because:
  - `action_executor.py` needs it to resolve `reference_media_id` on
    synthesizer-emitted `image_render` actions, and the previous
    inline `from gateway.routes.images import _resolve_uploaded_reference`
    introduced a `core layer → routes layer` cycle the architect's
    2026-04-29 review flagged as "waiting to break".
  - Routes import from core, not the other way around. This module is
    the core-layer home; routes/images.py keeps a tiny re-export so
    in-place callers don't break.
"""

from __future__ import annotations

from pathlib import Path


# Allowed extensions for uploaded reference images. Match what
# `routes/images.py::upload_media` accepts — keep these in sync.
_ALLOWED_EXTS: tuple[str, ...] = (".png", ".jpg", ".webp")


def is_safe_media_id(media_id: str) -> bool:
    """Reject media_ids that could escape the upload dir or cause
    surprising glob behaviour. Must be alphanumeric + ≤64 chars."""
    return bool(media_id) and media_id.isalnum() and len(media_id) <= 64


def resolve_uploaded_reference(
    state_dir: Path, media_id: str,
) -> Path | None:
    """Find an uploaded reference image by media_id.

    Tries each allowed extension in turn. Returns None if `media_id`
    fails the safety check or no matching file exists. Never follows
    symlinks (`is_file` returns False for non-files).
    """
    if not is_safe_media_id(media_id):
        return None
    base = state_dir / "media-uploads"
    for ext in _ALLOWED_EXTS:
        p = base / f"{media_id}{ext}"
        if p.is_file():
            return p
    return None
