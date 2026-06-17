"""`/v1/digest` — "what's new since last open" counts.

Surfaces a small object the Activity tab renders as a top card:
  {
    "since": <unix epoch s>,
    "new_images": <int>,
    "new_escalations": <int>,
    "new_pinned_turns": <int>,
    "completed_calendar_fires": <int>
  }

All four legs are best-effort — if a store is missing or its
underlying file/db is unreachable, that leg returns 0 rather than
500-ing the whole digest.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from gateway.deps import require_device, state
from gateway.routes.chat import _stable_user_id


router = APIRouter(prefix="/v1/digest", tags=["digest"])
log = logging.getLogger("gateway.digest")


class DigestCounts(BaseModel):
    since: int
    new_images: int = 0
    new_escalations: int = 0
    new_pinned_turns: int = 0
    completed_calendar_fires: int = 0


def _epoch_to_iso(epoch: int) -> str:
    return datetime.fromtimestamp(
        max(0, int(epoch)), tz=timezone.utc,
    ).isoformat(timespec="seconds")


@router.get("", response_model=DigestCounts)
def digest(
    since: int = Query(..., ge=0, description="unix epoch seconds"),
    device=Depends(require_device),
    request: Request = None,
) -> DigestCounts:
    st = state(request)
    counts = DigestCounts(since=since)

    # --- Recent images ---------------------------------------------------
    img_store = st.recent_images
    if img_store is not None:
        try:
            jobs = img_store.all_recent(since_ts=float(since))
            counts.new_images = len(jobs)
        except Exception as e:  # noqa: BLE001
            log.warning("digest: image leg failed: %s", e)

    # --- Escalations -----------------------------------------------------
    esc_store = st.escalation_store
    if esc_store is not None:
        try:
            since_iso = _epoch_to_iso(since)
            escs = esc_store.list(include_resolved=False)
            counts.new_escalations = sum(
                1 for e in escs if (e.reported_at or "") >= since_iso
            )
        except Exception as e:  # noqa: BLE001
            log.warning("digest: escalation leg failed: %s", e)

    # --- Pinned chat turns ----------------------------------------------
    try:
        vc = getattr(st, "vault_client", None)
        if vc is None:
            from shared.vault_client import VaultClient
            vc = VaultClient(
                vault_path=st.config.vault_path,
                daemon_host=st.config.vault_writer.host,
                daemon_port=st.config.vault_writer.port,
            )
        counts.new_pinned_turns = vc.count_chat_pinned_since(
            bot="terry",
            user_id=_stable_user_id(device.user),
            since_epoch=int(since),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("digest: pinned leg failed: %s", e)

    # --- Completed calendar fires ---------------------------------------
    cal = st.calendar_store
    if cal is not None:
        try:
            since_iso = _epoch_to_iso(since)
            jobs = cal.list(limit=500)
            # `last_run_at` is set when a fire actually executed (success
            # or error). We count any fire after `since` regardless of
            # outcome — a failed fire is still "something new" worth
            # surfacing.
            counts.completed_calendar_fires = sum(
                1 for j in jobs
                if (getattr(j, "last_run_at", None) or "") >= since_iso
            )
        except Exception as e:  # noqa: BLE001
            log.warning("digest: calendar leg failed: %s", e)

    return counts
