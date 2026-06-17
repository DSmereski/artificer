"""/v1/config — read-only client configuration the app needs at startup.

Currently exposes:
  - ntfy server URL + topic list (so the app's notification subscriber
    doesn't have to hardcode it).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from gateway.deps import require_device, state


router = APIRouter(prefix="/v1/config", tags=["config"])


# Topics the gateway publishes to. Keep in sync with ntfy.publish() call
# sites elsewhere — used by the Hive app to know what to subscribe.
_NTFY_TOPICS = [
    {"id": "ai-team-chat",     "label": "Terry replies",       "default_on": False},
    {"id": "ai-team-image",    "label": "Image renders done",  "default_on": True},
    {"id": "ai-team-video",    "label": "Video renders done",  "default_on": True},
    {"id": "ai-team-loras",    "label": "LoRA imports",        "default_on": True},
    {"id": "ai-team-recipes",  "label": "Image recipes saved", "default_on": True},
    {"id": "ai-team-calendar", "label": "Calendar jobs fired", "default_on": True},
    {"id": "ai-team-scout",    "label": "Scout alerts (GPU/disk)", "default_on": True},
    {"id": "ai-team",          "label": "General",             "default_on": False},
]


@router.get("/ntfy")
def ntfy_config(
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    st = state(request)
    ntfy = st.ntfy
    base_url = getattr(ntfy, "base_url", "") if ntfy is not None else ""
    enabled = getattr(ntfy, "enabled", False) if ntfy is not None else False
    return {
        "base_url": base_url,
        "enabled": enabled,
        "topics": _NTFY_TOPICS,
    }
