"""Composio client wrapper — optional, no-op when unconfigured.

Public surface:

  - `is_available()` -> bool
  - `ComposioClient.execute(app, action, args)` -> dict

`execute` always returns a dict shaped like
`{"ok": bool, "error": str | None, "result": Any}`. When the SDK is
not installed or `COMPOSIO_API_KEY` is missing, it returns
`{"ok": False, "error": "composio_unavailable", "result": None}`
without raising. That contract lets the synthesizer + the
`[saas_call]` action verb degrade cleanly.

We do *not* make any live SaaS call from this module's tests — the
real SDK is mocked or skipped. Every test path that would dial out
goes through `_invoke_sdk` which can be monkeypatched.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("gateway.composio")


def _has_sdk() -> bool:
    try:
        import composio_core  # type: ignore[import-not-found]  # noqa: F401
        return True
    except Exception:
        return False


def is_available() -> bool:
    """True iff the Composio SDK is importable AND an API key is set."""
    return bool(os.environ.get("COMPOSIO_API_KEY")) and _has_sdk()


@dataclass(frozen=True)
class ComposioResult:
    ok: bool
    error: str | None
    result: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "error": self.error, "result": self.result}


class ComposioClient:
    """Thin wrapper around the optional `composio-core` SDK.

    Constructor is total — never raises even with no key/SDK. Call
    `available` to check before issuing requests; `execute` returns
    a `composio_unavailable` result rather than raising so callers
    can keep their happy-path linear.
    """

    def __init__(self, *, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("COMPOSIO_API_KEY")
        self._sdk_present = _has_sdk()

    @property
    def available(self) -> bool:
        return bool(self._api_key) and self._sdk_present

    def execute(
        self, *, app: str, action: str, args: dict[str, Any] | None = None,
    ) -> ComposioResult:
        if not self.available:
            return ComposioResult(
                ok=False,
                error="composio_unavailable",
                result={
                    "missing_key": not self._api_key,
                    "missing_sdk": not self._sdk_present,
                },
            )
        if not isinstance(app, str) or not app.strip():
            return ComposioResult(ok=False, error="missing_app")
        if not isinstance(action, str) or not action.strip():
            return ComposioResult(ok=False, error="missing_action")
        try:
            payload = self._invoke_sdk(app, action, args or {})
        except Exception as e:  # noqa: BLE001
            log.exception("composio call %r/%r failed", app, action)
            return ComposioResult(ok=False, error=f"sdk_error: {e}")
        return ComposioResult(ok=True, error=None, result=payload)

    # -- seam for tests; production path imports the real SDK lazily. --
    def _invoke_sdk(
        self, app: str, action: str, args: dict[str, Any],
    ) -> Any:
        # Lazy import so a missing SDK never breaks the module load.
        from composio_core import Client  # type: ignore[import-not-found]
        client = Client(api_key=self._api_key)
        return client.actions.execute(app=app, action=action, params=args)


__all__ = ["ComposioClient", "ComposioResult", "is_available"]
